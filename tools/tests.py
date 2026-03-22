import os
import json
from datetime import datetime
from unittest.mock import patch

import numpy as np
from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from zoneinfo import ZoneInfo

from tools.models import ApiRelayService, UserApiRelayAccess, TardisRagEntry, TushareRagEntry, TTSOrder, TTSCreditAccount, EdgeInferenceOffer, EdgeInferenceRequest
from tools.qwen_runtime import QwenTTSRuntime
from tools.tardis_rag import extract_tardis_entries_from_text
from tools.tushare_rag import extract_tushare_entries_from_text
from tools.tts_config import estimate_total_chunks, get_tts_runtime_rules
from tools.tts_retention import should_archive_special_tts


class TTSConfigTests(SimpleTestCase):
    def test_default_runtime_rules(self):
        with patch.dict(os.environ, {}, clear=True):
            rules = get_tts_runtime_rules()
            self.assertEqual(rules['direct_max_chars'], 800)
            self.assertEqual(rules['chunk_chars'], 400)
            self.assertEqual(rules['batch_chars'], 800)

    def test_estimate_total_chunks_uses_direct_threshold(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(estimate_total_chunks(700), 1)
            self.assertEqual(estimate_total_chunks(2172), 6)

    def test_special_archive_cutoff_uses_2026_03_22_0500_shanghai(self):
        self.assertTrue(should_archive_special_tts(datetime(2026, 3, 21, 20, 59, tzinfo=ZoneInfo('UTC'))))
        self.assertFalse(should_archive_special_tts(datetime(2026, 3, 21, 21, 0, tzinfo=ZoneInfo('UTC'))))


class _FakeTalker:
    rope_deltas = 'sentinel'


class _FakeModel:
    def __init__(self):
        self.talker = _FakeTalker()


class _FakeTTS:
    def __init__(self, side_effect):
        self.model = _FakeModel()
        self._side_effect = side_effect
        self.calls = []

    def generate_custom_voice(self, **kwargs):
        self.calls.append(kwargs)
        return self._side_effect(**kwargs)


class QwenRuntimeBatchTests(SimpleTestCase):
    def test_generate_batch_audio_uses_true_batch_inference(self):
        runtime = QwenTTSRuntime()
        fake_tts = _FakeTTS(
            side_effect=lambda **kwargs: (
                [np.array([0.1], dtype=np.float32), np.array([0.2], dtype=np.float32)],
                24000,
            )
        )

        wavs, sr = runtime._generate_batch_audio(
            tts=fake_tts,
            batch_chunks=['第一段', '第二段'],
            language='Chinese',
            speaker='serena',
            instruct='保持自然',
            max_new_tokens=512,
        )

        self.assertEqual(sr, 24000)
        self.assertEqual(len(wavs), 2)
        self.assertEqual(len(fake_tts.calls), 1)
        self.assertEqual(fake_tts.calls[0]['text'], ['第一段', '第二段'])
        self.assertEqual(fake_tts.calls[0]['language'], ['Chinese', 'Chinese'])
        self.assertEqual(fake_tts.calls[0]['speaker'], ['serena', 'serena'])
        self.assertEqual(fake_tts.calls[0]['instruct'], ['保持自然', '保持自然'])
        self.assertIsNone(fake_tts.model.talker.rope_deltas)

    def test_generate_batch_audio_falls_back_to_serial_on_batch_error(self):
        progress_events = []

        def side_effect(**kwargs):
            texts = kwargs['text']
            if texts == ['第一段', '第二段']:
                raise RuntimeError('batch failed')
            if texts == ['第一段']:
                return [np.array([0.1], dtype=np.float32)], 24000
            if texts == ['第二段']:
                return [np.array([0.2], dtype=np.float32)], 24000
            raise AssertionError(f'unexpected texts: {texts}')

        runtime = QwenTTSRuntime()
        fake_tts = _FakeTTS(side_effect=side_effect)

        wavs, sr = runtime._generate_batch_audio(
            tts=fake_tts,
            batch_chunks=['第一段', '第二段'],
            language='Chinese',
            speaker='serena',
            instruct='保持自然',
            max_new_tokens=512,
            progress_callback=lambda phase, **payload: progress_events.append((phase, payload)),
            batch_index=1,
            total_batches=1,
        )

        self.assertEqual(sr, 24000)
        self.assertEqual(len(wavs), 2)
        self.assertEqual([call['text'] for call in fake_tts.calls], [['第一段', '第二段'], ['第一段'], ['第二段']])
        self.assertEqual(progress_events[0][0], 'batch_fallback')
        self.assertIn('batch_generation_error:RuntimeError', progress_events[0][1]['reason'])

    def test_generate_batch_items_returns_per_item_errors_after_batch_fallback(self):
        progress_events = []

        def side_effect(**kwargs):
            texts = kwargs['text']
            if texts == ['第一段', '第二段']:
                raise RuntimeError('batch failed')
            if texts == ['第一段']:
                return [np.array([0.1], dtype=np.float32)], 24000
            if texts == ['第二段']:
                raise ValueError('bad text')
            raise AssertionError(f'unexpected texts: {texts}')

        runtime = QwenTTSRuntime()
        fake_tts = _FakeTTS(side_effect=side_effect)
        runtime.tts = fake_tts

        results, sr = runtime.generate_batch_items(
            items=[
                {'text': '第一段', 'language': 'Chinese', 'speaker': 'serena', 'instruct': '保持自然', 'max_new_tokens': 512},
                {'text': '第二段', 'language': 'Chinese', 'speaker': 'serena', 'instruct': '保持自然', 'max_new_tokens': 512},
            ],
            progress_callback=lambda phase, **payload: progress_events.append((phase, payload)),
        )

        self.assertEqual(sr, 24000)
        self.assertEqual(len(results), 2)
        self.assertIsInstance(results[1], ValueError)
        self.assertEqual(progress_events[0][0], 'batch_fallback')


class ApiRelayTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='relayuser', email='relay@example.com', password='secret123')
        self.service = ApiRelayService.objects.create(
            slug='demo-api',
            name='Demo API',
            base_url='http://127.0.0.1:9001',
            is_active=True,
            require_api_key=True,
            require_login=False,
            require_manual_approval=True,
            allowed_methods='GET,POST',
            timeout_seconds=30,
            upstream_headers='{"Authorization":"Bearer upstream-secret","X-Upstream-Flag":"1"}',
            upstream_query_params='{"api_key":"abc123","fixed":"yes"}',
            public_path='/api-relay/demo-api/',
            description='demo',
            example_paths='/health\n/v1/items',
        )

    def test_api_relay_requires_api_key(self):
        response = self.client.get('/api-relay/demo-api/health')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['error'], 'api_key_required')

    def test_api_relay_rejects_invalid_api_key(self):
        response = self.client.get('/api-relay/demo-api/health', HTTP_X_API_KEY='atk_badprefix.badsecret')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error'], 'invalid_api_key')

    def test_api_relay_rejects_key_without_permission(self):
        access = UserApiRelayAccess.objects.create(user=self.user, service=self.service, is_enabled=False)
        raw_key = access.issue_api_key()
        access.save()
        response = self.client.get('/api-relay/demo-api/health', HTTP_X_API_KEY=raw_key)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error'], 'permission_denied')

    @patch('tools.views.RELAY_HTTP_SESSION.request')
    def test_api_relay_forwards_with_injected_headers_and_query(self, mock_request):
        access = UserApiRelayAccess.objects.create(
            user=self.user,
            service=self.service,
            is_enabled=True,
            approved_at=datetime(2026, 3, 21, 12, 0, tzinfo=ZoneInfo('UTC')),
        )
        raw_key = access.issue_api_key()
        access.save()

        class _FakeResponse:
            status_code = 200
            content = b'{"ok": true}'
            headers = {'Content-Type': 'application/json'}

        mock_request.return_value = _FakeResponse()
        response = self.client.get(
            '/api-relay/demo-api/health',
            {'client_param': 'from-user', 'api_key': 'user-override-attempt'},
            HTTP_X_API_KEY=raw_key,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Api-Relay-Service'], 'demo-api')
        _, kwargs = mock_request.call_args
        self.assertEqual(kwargs['url'], 'http://127.0.0.1:9001/health')
        self.assertEqual(kwargs['params']['client_param'], 'from-user')
        self.assertEqual(kwargs['params']['api_key'], 'abc123')
        self.assertEqual(kwargs['params']['fixed'], 'yes')
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer upstream-secret')
        self.assertEqual(kwargs['headers']['X-Upstream-Flag'], '1')
        self.assertEqual(kwargs['headers']['X-Ai-Tools-Username'], 'relayuser')
        self.assertNotIn('Cookie', kwargs['headers'])

    @patch('tools.views.RELAY_HTTP_SESSION.get')
    def test_quant_article_tushare_renders_catalog_examples(self, mock_get):
        class _CatalogResponse:
            ok = True

            @staticmethod
            def json():
                return {
                    'categories': {'互动易问答（沪深）': ['irm_qa_sh']},
                    'examples': {
                        '互动易问答（沪深）': [
                            {
                                'api_name': 'irm_qa_sh',
                                'params': {'ts_code': '600000.SH'},
                                'example_url': '/pro/irm_qa_sh?ts_code=600000.SH',
                            }
                        ]
                    },
                }

        mock_get.return_value = _CatalogResponse()
        response = self.client.get('/quant/tushare-pro-guide/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '站内可用目录与示例参数')
        self.assertContains(response, 'ETF 数据')
        self.assertContains(response, '2026-12-29')
        self.assertContains(response, 'irm_qa_sh')
        self.assertContains(response, '/tushare/pro/irm_qa_sh?ts_code=600000.SH')

    @patch('tools.views.RELAY_HTTP_SESSION.get')
    def test_quant_tushare_catalog_page_renders_searchable_directory(self, mock_get):
        class _CatalogResponse:
            ok = True

            @staticmethod
            def json():
                return {
                    'categories': {'融资融券基础数据': ['margin_detail']},
                    'examples': {
                        '融资融券基础数据': [
                            {
                                'api_name': 'margin_detail',
                                'params': {'trade_date': '20260320', 'ts_code': '000002.SZ'},
                                'fields': 'trade_date,ts_code,name,rzye,rzmre',
                                'example_url': '/pro/margin_detail?trade_date=20260320&ts_code=000002.SZ',
                                'retention_policy': {
                                    'label': '通常到北京时间当日 24:00',
                                    'recommended_refresh': '交易日收盘后到晚间补齐阶段最值得刷新',
                                    'reason': '这类数据通常按日结算或按日披露，当日内重复值较高。',
                                },
                            }
                        ]
                    },
                    'cache_policy': {'historical_date_queries': '通常 3 到 7 天'},
                }

        mock_get.return_value = _CatalogResponse()
        response = self.client.get('/quant/tushare-pro-catalog/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '可搜索前端目录页')
        self.assertContains(response, 'margin_detail')
        self.assertContains(response, '推荐 fields')
        self.assertContains(response, '推荐保留周期')
        self.assertContains(response, '推荐访问节奏')
        self.assertContains(response, 'Python 示例')
        self.assertContains(response, 'import requests')
        self.assertContains(response, 'session.trust_env = False')
        self.assertContains(response, 'URL = &quot;https://ai-tool.indevs.in/tushare/pro/margin_detail&quot;')
        self.assertContains(response, '点击测试')
        self.assertContains(response, '/tushare/pro/margin_detail?trade_date=20260320&amp;ts_code=000002.SZ')

    @patch('tools.views.RELAY_HTTP_SESSION.get')
    def test_tushare_proxy_catalog_renders_html_for_browser_without_key(self, mock_get):
        class _CatalogResponse:
            ok = True

            @staticmethod
            def json():
                return {
                    'categories': {'互动易问答（沪深）': ['irm_qa_sh']},
                    'examples': {
                        '互动易问答（沪深）': [
                            {
                                'api_name': 'irm_qa_sh',
                                'params': {'ts_code': '600000.SH'},
                                'example_url': '/pro/irm_qa_sh?ts_code=600000.SH',
                            }
                        ]
                    },
                }

        mock_get.return_value = _CatalogResponse()
        response = self.client.get('/tushare/pro/catalog', HTTP_ACCEPT='text/html,application/xhtml+xml')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '可搜索前端目录页')
        self.assertContains(response, 'irm_qa_sh')

    @patch('tools.views.RELAY_HTTP_SESSION.request')
    def test_tushare_proxy_catalog_with_plain_api_key(self, mock_request):
        service = ApiRelayService.objects.create(
            slug='placeholder',
            name='Placeholder',
            base_url='http://127.0.0.1:9002',
            is_active=True,
        )
        service.delete()
        service = ApiRelayService.objects.filter(slug='tushare').first()
        if service is None:
            service = ApiRelayService.objects.create(
                slug='tushare',
                name='Tushare Relay',
                base_url='http://127.0.0.1:8001',
                is_active=True,
                require_api_key=True,
                require_login=False,
                require_manual_approval=True,
                allowed_methods='GET,POST',
                timeout_seconds=30,
                public_path='/tushare/',
            )
        key_user = User.objects.create_user(username='tushare_catalog_key')
        UserApiRelayAccess.objects.create(
            user=key_user,
            service=service,
            is_enabled=True,
            approved_at=datetime(2026, 3, 21, 12, 0, tzinfo=ZoneInfo('UTC')),
            api_key_prefix='plainkey',
            api_key_secret_hash=make_password('plainkey'),
            api_key_last4='nkey',
        )

        class _FakeResponse:
            status_code = 200
            content = json.dumps({'code': 0, 'examples': {'股票': []}}).encode()
            headers = {'Content-Type': 'application/json'}

        mock_request.return_value = _FakeResponse()
        response = self.client.get('/tushare/pro/catalog', HTTP_X_API_KEY='plainkey')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['code'], 0)
        _, kwargs = mock_request.call_args
        self.assertEqual(kwargs['url'], 'http://127.0.0.1:8001/pro/catalog')


class TardisRagTests(TestCase):
    def test_tardis_rag_returns_pricing_answer(self):
        response = self.client.post(
            '/quant/tardis-data-guide/rag/',
            data='{"question":"整月租用高档 API 多少钱？"}',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertIn('1500 元每月', payload['answer'])

    def test_tardis_rag_rejects_invalid_json(self):
        response = self.client.post(
            '/quant/tardis-data-guide/rag/',
            data='{bad json',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'invalid_json')

    def test_tardis_superadmin_can_login_and_save_entry(self):
        page_response = self.client.get('/quant/tardis-data-guide/')
        self.assertEqual(page_response.status_code, 200)

        login_response = self.client.post(
            '/quant/tardis-data-guide/admin/login/',
            data={'username': 'zhanyuting', 'password': 'zhanyuting'},
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertTrue(login_response.json()['ok'])

        save_response = self.client.post(
            '/quant/tardis-data-guide/admin/entries/save/',
            data='{"title":"妹宝历史答复","question_hint":"妹宝 包月","keywords":"妹宝,包月,专属","answer":"妹宝专属说明：包月还是 1500 元，但可以直接把历史问答复制给客服。","sort_order":5,"is_active":true}',
            content_type='application/json',
        )
        self.assertEqual(save_response.status_code, 200)
        self.assertTrue(save_response.json()['ok'])
        self.assertEqual(TardisRagEntry.objects.count(), 1)

        rag_response = self.client.post(
            '/quant/tardis-data-guide/rag/',
            data='{"question":"妹宝包月怎么问？"}',
            content_type='application/json',
        )
        self.assertEqual(rag_response.status_code, 200)
        self.assertIn('可以直接把历史问答复制给客服', rag_response.json()['answer'])

    def test_tardis_rag_matches_equivalent_delivery_questions(self):
        TardisRagEntry.objects.create(
            title='发货形式说明',
            question_hint='发货形式,交付方式,怎么发给我',
            keywords='发货,交付,链接,发送',
            answer='数据通常通过定制链接或整理后的交付方式发给你，具体按你购买的档位来定。',
            sort_order=1,
            is_active=True,
        )
        response = self.client.post(
            '/quant/tardis-data-guide/rag/',
            data='{"question":"数据你怎么发给我呢？"}',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('定制链接', response.json()['answer'])

    def test_tardis_superadmin_can_extract_multiple_entries_from_bulk_text(self):
        self.client.post(
            '/quant/tardis-data-guide/admin/login/',
            data={'username': 'zhanyuting', 'password': 'zhanyuting'},
        )
        save_response = self.client.post(
            '/quant/tardis-data-guide/admin/entries/save/',
            data=json.dumps(
                {
                    'source_text': (
                        '包月的话目前是 1500 元每月，适合大量历史数据需求或团队型下载。\n\n'
                        '数据一般通过定制链接或者整理好的交付方式发给你，按购买档位来安排。\n\n'
                        '需要的话直接加微信 15180066256。'
                    ),
                    'keywords': '妹宝,历史答复',
                    'sort_order': 10,
                }
            ),
            content_type='application/json',
        )
        self.assertEqual(save_response.status_code, 200)
        payload = save_response.json()
        self.assertTrue(payload['ok'])
        self.assertGreaterEqual(payload['created_count'], 3)

        rag_response = self.client.post(
            '/quant/tardis-data-guide/rag/',
            data='{"question":"数据你怎么发给我呢？"}',
            content_type='application/json',
        )
        self.assertEqual(rag_response.status_code, 200)
        self.assertIn('定制链接', rag_response.json()['answer'])

    def test_extract_tardis_entries_from_text_supports_explicit_qa(self):
        entries = extract_tardis_entries_from_text(
            '问：发货形式是什么？\n答：一般通过链接发你。\n\n问：包月多少钱？\n答：1500 元每月。'
        )
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]['title'], '发货形式是什么？')
        self.assertIn('1500', entries[1]['answer'])

    def test_extract_tardis_entries_from_plain_paragraph_creates_multiple_topics(self):
        entries = extract_tardis_entries_from_text(
            '包月的话目前是1500元每月。数据一般通过定制链接发给你。需要的话直接加微信15180066256。'
        )
        self.assertGreaterEqual(len(entries), 3)


class TushareRagTests(TestCase):
    def test_tushare_page_renders_rag_shell(self):
        response = self.client.get('/quant/tushare-pro-guide/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '在线客服 · 页面内 RAG')
        self.assertContains(response, 'Tushare 客服语料')

    def test_tushare_superadmin_can_login_and_save_entry(self):
        self.client.get('/quant/tushare-pro-guide/')
        login_response = self.client.post(
            '/quant/tushare-pro-guide/admin/login/',
            data={'username': 'zhanyuting', 'password': 'zhanyuting'},
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertTrue(login_response.json()['ok'])

        save_response = self.client.post(
            '/quant/tushare-pro-guide/admin/entries/save/',
            data=json.dumps(
                {
                    'source_text': (
                        'Tushare 现在只支持 API Key，不再支持网页登录拿权限。'
                        ' 分钟数据当前不开放在站内 replay。'
                        ' 如果要目录可以看 /tushare/pro/catalog。'
                    ),
                    'keywords': '权限,分钟,目录',
                    'sort_order': 10,
                }
            ),
            content_type='application/json',
        )
        self.assertEqual(save_response.status_code, 200)
        payload = save_response.json()
        self.assertTrue(payload['ok'])
        self.assertGreaterEqual(payload['created_count'], 3)
        self.assertEqual(TushareRagEntry.objects.count(), payload['created_count'])

        rag_response = self.client.post(
            '/quant/tushare-pro-guide/rag/',
            data='{"question":"分钟数据能不能通过站内接口拿？"}',
            content_type='application/json',
        )
        self.assertEqual(rag_response.status_code, 200)
        self.assertIn('不开放', rag_response.json()['answer'])

    def test_tushare_extract_entries_from_plain_paragraph_creates_multiple_topics(self):
        entries = extract_tushare_entries_from_text(
            'Tushare 现在只支持 API Key。分钟数据当前不开放在站内 replay。目录可以看 /tushare/pro/catalog。'
        )
        self.assertGreaterEqual(len(entries), 3)


class TTSRegenerateTests(TestCase):
    def test_regenerate_creates_new_order_for_delivered_order(self):
        user = User.objects.create_user(username='ttsuser', email='tts@example.com', password='secret123')
        TTSCreditAccount.objects.create(
            user=user,
            is_unlimited=False,
            char_balance=1000,
            total_purchased_chars=1000,
            total_used_chars=100,
        )
        original_order = TTSOrder.objects.create(
            user=user,
            contact_name='ttsuser',
            email='tts@example.com',
            source_text='这是需要重新生成的文本',
            voice_preset=TTSOrder.VoicePreset.SERENA,
            style_notes='保持自然',
            business_usage=True,
            delivery_format=TTSOrder.DeliveryFormat.MP3,
            estimated_price='0.00',
            final_price='0.00',
            payment_status=TTSOrder.PaymentStatus.PAID,
            status=TTSOrder.Status.DELIVERED,
            payment_reference='CREDIT-OLD',
            paid_at=datetime(2026, 3, 21, 12, 0, tzinfo=ZoneInfo('UTC')),
            payment_verified_at=datetime(2026, 3, 21, 12, 0, tzinfo=ZoneInfo('UTC')),
            delivered_at=datetime(2026, 3, 21, 12, 2, tzinfo=ZoneInfo('UTC')),
        )

        self.client.force_login(user)
        response = self.client.post(f'/tts-studio/regenerate/{original_order.order_no}/', {'email': 'tts@example.com'})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(TTSOrder.objects.count(), 2)
        new_order = TTSOrder.objects.exclude(pk=original_order.pk).get()
        self.assertEqual(new_order.status, TTSOrder.Status.QUEUED)
        self.assertEqual(new_order.source_text, original_order.source_text)
        self.assertIn(new_order.order_no, payload['redirect_url'])


class EdgeInferenceTests(TestCase):
    def test_edge_inference_hub_renders_and_accepts_request(self):
        offer = EdgeInferenceOffer.objects.create(
            slug='rtx5090-edge',
            name='RTX 5090 Edge',
            provider='local',
            gpu_name='RTX 5090',
            gpu_count=1,
            vram_gb='32.0',
            cpu_cores=24,
            ram_gb=128,
            disk_gb=2000,
            region='CN-Shanghai',
            network_up_mbps=1000,
            network_down_mbps=1000,
            billing_unit=EdgeInferenceOffer.BillingUnit.HOUR,
            price='12.50',
            min_rental_hours=1,
            stock=2,
            supported_models='vLLM, SGLang, Ollama',
            endpoint_protocols='OpenAI Compatible, SSH',
            is_active=True,
        )
        get_response = self.client.get('/edge-inference/')
        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, 'RTX 5090 Edge')

        post_response = self.client.post(
            '/edge-inference/',
            data={
                'offer_id': offer.id,
                'contact_name': 'Alice',
                'email': 'alice@example.com',
                'wechat': 'alice_wechat',
                'requested_model': 'Qwen3-32B',
                'use_case': '我要一个可公网访问的边缘推理实例，用来做 API 推理服务。',
                'expected_concurrency': 8,
                'expected_hours': 24,
                'budget': '300',
            },
        )
        self.assertEqual(post_response.status_code, 200)
        self.assertEqual(EdgeInferenceRequest.objects.count(), 1)
        req = EdgeInferenceRequest.objects.get()
        self.assertEqual(req.offer, offer)
        self.assertEqual(req.requested_model, 'Qwen3-32B')

    def test_edge_inference_request_can_issue_access_key(self):
        req = EdgeInferenceRequest.objects.create(
            contact_name='Bob',
            email='bob@example.com',
            requested_model='vLLM',
            use_case='我要一个可访问的推理入口。',
        )
        raw_key = req.issue_access_key()
        req.public_endpoint = 'https://ai-tool.indevs.in/api-relay/edge-demo/'
        req.ssh_host = 'ai-tool.indevs.in'
        req.ssh_username = 'user'
        req.save()
        self.assertTrue(raw_key.startswith('eik_'))
        self.assertTrue(req.api_key_prefix.startswith('eik_'))
        self.assertEqual(req.ssh_host, 'ai-tool.indevs.in')

    def test_edge_inference_offer_can_bind_real_api_relay_access(self):
        user = User.objects.create_user(username='edgeuser', password='pass123', email='edge@example.com')
        service = ApiRelayService.objects.create(
            slug='edge-demo',
            name='Edge Demo Relay',
            base_url='http://127.0.0.1:8999',
            is_active=True,
            require_api_key=True,
            public_path='/api-relay/edge-demo/',
        )
        offer = EdgeInferenceOffer.objects.create(
            slug='edge-offer',
            name='Edge Offer',
            provider='local',
            gpu_name='RTX 5090',
            relay_service=service,
            vram_gb='32.0',
            price='12.50',
            stock=1,
        )
        req = EdgeInferenceRequest.objects.create(
            user=user,
            offer=offer,
            contact_name='Edge User',
            email='edge@example.com',
            requested_model='Qwen',
            use_case='需要真实可调用的 relay 入口。',
        )
        access, created = UserApiRelayAccess.objects.get_or_create(user=user, service=service)
        self.assertTrue(created)
        raw_key = access.issue_api_key()
        access.is_enabled = True
        access.approved_at = timezone.now()
        access.save()

        req.apply_relay_access(access)
        req.save(update_fields=['public_endpoint', 'api_key_prefix', 'api_key_secret_hash', 'api_key_last4', 'api_key_created_at', 'updated_at'])

        self.assertTrue(raw_key.startswith('atk_'))
        self.assertEqual(req.public_endpoint, 'https://ai-tool.indevs.in/api-relay/edge-demo/')
        self.assertEqual(req.api_key_prefix, access.api_key_prefix)
        self.assertEqual(req.api_key_last4, access.api_key_last4)
