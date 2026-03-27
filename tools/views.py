import os
import json
import hashlib
import math
import secrets
import subprocess
import sys
import re
import tempfile
import fcntl
import time as time_module
from functools import lru_cache
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
from urllib.parse import urlsplit, urlencode
import markdown
import requests
import akshare as ak
from decimal import Decimal, InvalidOperation
from base64 import b64encode
from io import BytesIO
from pathlib import Path

from django.contrib.auth.hashers import check_password
from django.shortcuts import render, get_object_or_404, redirect
from django.http import FileResponse, HttpResponse, Http404, JsonResponse, HttpResponseForbidden
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import IntegrityError, OperationalError as DBOperationalError, ProgrammingError, transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_POST
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from .forms import (
    TTSOrderForm,
    TTSOrderLookupForm,
    TTSPaymentProofForm,
    TTSCreditLoginForm,
    TTSCreditRegisterForm,
    TTSRechargeForm,
    TTSCreditConsumeForm,
    TTSCreditRechargeProofForm,
    EdgeInferenceRequestForm,
    CodexBriefingForm,
)
from .models import Category, Tool, TopicPage, ColumnPage, ColumnDailyView, ToolDailyView, TTSOrder, TTSCreditAccount, TTSCreditRechargeOrder, TTSCreditLedger, ApiRelayService, UserApiRelayAccess, TushareNewsCache, TushareReplayLease, TardisRagEntry, TushareRagEntry, SocialRadarTask, CodexBriefingTask, EdgeInferenceOffer, EdgeInferenceRequest
from .tts_config import get_tts_runtime_rules, estimate_total_chunks
from .tts import VOICE_PRESET_CONFIG, build_quote, build_turnaround, build_recharge_amount, DEFAULT_RECHARGE_PACKS
from .tts_jobs import stop_tts_worker, trigger_tts_generation
from .tardis_rag import answer_tardis_question, build_dynamic_chunks, extract_tardis_entries_from_text
from .tushare_rag import answer_tushare_question, build_dynamic_chunks as build_tushare_dynamic_chunks, extract_tushare_entries_from_text
from .social_radar_jobs import trigger_social_radar_worker
from .codex_briefing_jobs import trigger_codex_briefing_worker
from .social_radar_runtime import cleanup_expired_social_radar_results, get_task_result_dir, list_task_result_files, sanitize_social_radar_params
from .tts_retention import archive_tts_file
import qrcode


PROGRESS_RE = re.compile(r'\[进度\s*(\d+)%\]\s*(.+)$')
CHUNK_PROGRESS_RE = re.compile(
    r'已生成\s*(?P<done_chars>\d+)/(?P<total_chars>\d+)\s*字，当前第\s*'
    r'(?P<current_chunk>\d+)/(?P<total_chunks>\d+)\s*段，本段\s*(?P<chunk_chars>\d+)\s*字'
)
CHUNK_BATCH_RE = re.compile(r'第\s*(?P<batch_start>\d+)-(?P<batch_end>\d+)\s*段\s*/\s*共\s*(?P<total_chunks>\d+)\s*段')
CHUNK_TOTAL_RE = re.compile(r'切成\s*(?P<total_chunks>\d+)\s*段')
WHOLE_TEXT_RE = re.compile(r'已生成\s*(?P<done_chars>\d+)/(?P<total_chars>\d+)\s*字，整段音频生成完成，本次共\s*(?P<chunk_chars>\d+)\s*字')
MANUAL_PAYMENT_NOTICE = (
    '支付方式是在网页端注册后根据网页二维码进行微信支付。支付后加本人微信 dreamsjtuai 发送付款截图，'
    '并提供咱们 TTS section 注册的邮箱，本人收到后会更改该邮箱的额度。有了额度就可以自动生成 TTS。'
)
TUSHARE_RELAY_BASE_URL = os.getenv('TUSHARE_RELAY_BASE_URL', 'http://127.0.0.1:8001').rstrip('/')
TARDIS_SUPERADMIN_SESSION_KEY = 'tardis_superadmin_authed'
TARDIS_SUPERADMIN_USERNAME = os.getenv('TARDIS_SUPERADMIN_USERNAME', 'zhanyuting')
TARDIS_SUPERADMIN_PASSWORD = os.getenv('TARDIS_SUPERADMIN_PASSWORD', 'zhanyuting')
TUSHARE_SUPERADMIN_SESSION_KEY = 'tushare_superadmin_authed'
TUSHARE_SUPERADMIN_USERNAME = os.getenv('TUSHARE_SUPERADMIN_USERNAME', 'zhanyuting')
TUSHARE_SUPERADMIN_PASSWORD = os.getenv('TUSHARE_SUPERADMIN_PASSWORD', 'zhanyuting')
RELAY_HTTP_SESSION = requests.Session()
RELAY_HTTP_SESSION.trust_env = False
TUSHARE_DIRECT_HTTP_SESSION = requests.Session()
TUSHARE_DIRECT_HTTP_SESSION.trust_env = False
TUSHARE_A_SHARE_MIN_HTTP_SESSION = requests.Session()
TUSHARE_A_SHARE_MIN_HTTP_SESSION.trust_env = False
TUSHARE_FUTURES_MIN_HTTP_SESSION = requests.Session()
TUSHARE_FUTURES_MIN_HTTP_SESSION.trust_env = False
TUSHARE_NEWS_CACHE_TTL = timedelta(hours=1)
TUSHARE_NEWS_CACHE_PURGE_TTL = timedelta(days=3)
TUSHARE_MAJOR_NEWS_CACHE_TTL = timedelta(days=1)
TUSHARE_MAJOR_NEWS_CACHE_PURGE_TTL = timedelta(days=7)
TUSHARE_IRM_QA_CACHE_TTL = timedelta(hours=12)
TUSHARE_IRM_QA_CACHE_PURGE_TTL = timedelta(days=7)
TUSHARE_EXPRESS_NEWS_CACHE_TTL = timedelta(minutes=15)
TUSHARE_EXPRESS_NEWS_CACHE_PURGE_TTL = timedelta(days=2)
TUSHARE_ANALYST_RANK_CACHE_TTL = timedelta(days=1)
TUSHARE_ANALYST_RANK_CACHE_PURGE_TTL = timedelta(days=7)
TUSHARE_ANALYST_DETAIL_CACHE_TTL = timedelta(hours=12)
TUSHARE_ANALYST_DETAIL_CACHE_PURGE_TTL = timedelta(days=7)
TUSHARE_ANALYST_HISTORY_CACHE_TTL = timedelta(days=1)
TUSHARE_ANALYST_HISTORY_CACHE_PURGE_TTL = timedelta(days=14)
TUSHARE_RESEARCH_REPORT_CACHE_TTL = timedelta(hours=12)
TUSHARE_RESEARCH_REPORT_CACHE_PURGE_TTL = timedelta(days=7)
TUSHARE_STATUS_CACHE_TTL = timedelta(minutes=5)
TUSHARE_STATUS_CACHE_PURGE_TTL = timedelta(days=1)
TUSHARE_CATALOG_CACHE_TTL = timedelta(hours=6)
TUSHARE_CATALOG_CACHE_PURGE_TTL = timedelta(days=7)
TUSHARE_GENERIC_CURRENT_CACHE_TTL = timedelta(hours=6)
TUSHARE_GENERIC_CURRENT_CACHE_PURGE_TTL = timedelta(days=14)
TUSHARE_GENERIC_HISTORY_CACHE_TTL = timedelta(days=7)
TUSHARE_GENERIC_HISTORY_CACHE_PURGE_TTL = timedelta(days=30)
TUSHARE_DAILY_LATEST_CACHE_TTL = timedelta(minutes=30)
TUSHARE_DAILY_LATEST_CACHE_PURGE_TTL = timedelta(days=7)
TUSHARE_REPLAY_CACHE_SWEEP_INTERVAL = timedelta(minutes=10)
TUSHARE_REPLAY_CACHE_FALLBACK_PURGE_TTL = timedelta(days=30)
TUSHARE_REPLAY_LEASE_TTL = timedelta(seconds=45)
TUSHARE_REPLAY_FOLLOWER_WAIT_TIMEOUT = timedelta(seconds=50)
TUSHARE_REPLAY_FOLLOWER_POLL_INTERVAL_SECONDS = 0.2
TUSHARE_REPLAY_LOCK_DIR = Path(tempfile.gettempdir()) / 'tushare_replay_cache_locks'
TUSHARE_REPLAY_SWEEP_MARKER = TUSHARE_REPLAY_LOCK_DIR / '.global_cleanup_at'
TEMP_TUSHARE_API_KEY = '20260323'
TEMP_TUSHARE_API_KEY_DATE = date(2026, 3, 23)
TEMP_TUSHARE_API_KEY_CUTOFF = time(18, 0)
DEFAULT_TUSHARE_RT_MIN_TOKENS = (
    'aa5388c77aa253d9a65fca973a0b79c9182b810b7f5ec7caa317a2a4,'
    '009c49c7abe2f2bd16c823d4d8407f7e7fcbbc1883bf50eaae90ae5f,'
    'cc63dba54752a8ed6d7351c56e15f1ddc95e11b39b49d8fef395a2a9,'
    '8a13051b514249491b029cb46bcf1cd4e059b83bdeb516fc53c9f630,'
    '3531aac4e2b7e3752304be0e83df5c39a2977fa57aa0e5e43fe16a38,'
    'f1ce53736e3b6777425d3df97c05e7460a55534db8ece60114c9e2a3,'
    '6a6b8bf5108aa2cc83d3ba23005fdb06323f208383992dd5e77a3d76,'
    '577f92d0103aa2c27d6c458f8b67817ccacd487492b50ce107d04fb0,'
    '7e0df022a3af325bdf68870f9ca63abd4c8c79d35c1eaef2c3a69a98,'
    '5e32fc5444690de433fdab31c3b5f479f6b2f74083c9186299f0b8fe,'
    'adcf5802584d3f341ad1daadc82e3cc181cdfe52b2055493a5342e3f'
)
TUSHARE_RT_MIN_ALLOWED_FREQ = '30MIN'
TUSHARE_A_SHARE_MIN_ALLOWED_FREQS = {
    '1MIN': 1,
    '5MIN': 5,
    '15MIN': 15,
    '30MIN': 30,
    '60MIN': 60,
}
TUSHARE_FUTURES_MIN_ALLOWED_FREQS = {
    '1MIN': 1,
    '5MIN': 5,
    '15MIN': 15,
    '30MIN': 30,
}
TUSHARE_MINUTE_ALLOWED_FREQS = {**TUSHARE_A_SHARE_MIN_ALLOWED_FREQS, **TUSHARE_FUTURES_MIN_ALLOWED_FREQS}
TUSHARE_A_SHARE_TS_CODE_RE = re.compile(r'^\d{6}\.(?:SZ|SH)$', re.I)
TUSHARE_FUTURES_SYMBOL_RE = re.compile(r'^[A-Z]{1,4}\d{0,4}$', re.I)
TUSHARE_FUTURES_SYMBOL_WITH_EXCHANGE_RE = re.compile(
    r'^([A-Z]{1,4}\d{0,4})(?:\.(?:SHF|SHFE|DCE|CZC|CZCE|INE|CFE|GFEX))?$',
    re.I,
)
TUSHARE_RT_MIN_UPSTREAM_URL = 'http://api.tushare.pro'
TUSHARE_A_SHARE_MIN_UPSTREAM_URL = 'https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData'
TUSHARE_FUTURES_MIN_UPSTREAM_URL = 'https://stock2.finance.sina.com.cn/futures/api/jsonp.php/=/InnerFuturesNewService.getFewMinLine'
SOCIAL_RADAR_TYPE_LABELS = {
    SocialRadarTask.TaskType.KEYWORD: 'X 关键词抓取',
    SocialRadarTask.TaskType.X_ZHIHU_SEARCH: 'X + 知乎联合搜索',
    SocialRadarTask.TaskType.FOLLOWING: 'X 关注流',
    SocialRadarTask.TaskType.USER_TIMELINE: 'X 用户历史推文',
    SocialRadarTask.TaskType.USER_FOLLOWING: 'X 用户关注列表',
    SocialRadarTask.TaskType.ZHIHU_QUESTION: '知乎问题回答',
    SocialRadarTask.TaskType.ZHIHU_SEARCH: '知乎关键词搜索',
    SocialRadarTask.TaskType.ZHIHU_USER: '知乎用户动态',
    SocialRadarTask.TaskType.XIAOHONGSHU_USER: '小红书博主笔记',
    SocialRadarTask.TaskType.XIAOHONGSHU_SEARCH: '小红书关键词搜索',
    SocialRadarTask.TaskType.FOLO: 'Folo 时间线',
}
CODEX_BRIEFING_API_KEY = os.getenv('CODEX_BRIEFING_API_KEY', 'huanghanchi')
CODEX_BRIEFING_SESSION_AUTH_KEY = 'codex_briefing_api_authed'


def _parse_json_mapping(raw_value: str) -> dict[str, str]:
    if not raw_value:
        return {}
    try:
        data = json.loads(raw_value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): '' if value is None else str(value) for key, value in data.items()}


def _ensure_session_key(request) -> str:
    if not request.session.session_key:
        request.session.create()
    return request.session.session_key


def _is_codex_briefing_authed(request) -> bool:
    return bool(request.session.get(CODEX_BRIEFING_SESSION_AUTH_KEY))


def _build_codex_briefing_task_payload(task: CodexBriefingTask) -> dict:
    return {
        'id': task.id,
        'status': task.status,
        'status_display': task.get_status_display(),
        'stage': task.stage,
        'progress': task.progress,
        'message': task.message,
        'error': task.error,
        'summary_title': task.summary_title,
        'summary_text': task.summary_text,
        'rendered_html': task.rendered_html,
        'source_char_count': task.source_char_count,
        'created_at': timezone.localtime(task.created_at).strftime('%Y-%m-%d %H:%M:%S') if task.created_at else '',
        'started_at': timezone.localtime(task.started_at).strftime('%Y-%m-%d %H:%M:%S') if task.started_at else '',
        'finished_at': timezone.localtime(task.finished_at).strftime('%Y-%m-%d %H:%M:%S') if task.finished_at else '',
    }


def _is_tardis_superadmin(request) -> bool:
    return bool(request.session.get(TARDIS_SUPERADMIN_SESSION_KEY))


def _get_tardis_rag_entries():
    return list(TardisRagEntry.objects.filter(is_active=True).order_by('sort_order', '-updated_at', '-id'))


def _is_tushare_superadmin(request) -> bool:
    return bool(request.session.get(TUSHARE_SUPERADMIN_SESSION_KEY))


def _get_tushare_rag_entries():
    return list(TushareRagEntry.objects.filter(is_active=True).order_by('sort_order', '-updated_at', '-id'))


def _build_social_radar_task_payload(task: SocialRadarTask) -> dict:
    run_dir = get_task_result_dir(task)
    fulltext = list_task_result_files(task)
    return {
        'id': task.id,
        'task_type': task.task_type,
        'task_label': SOCIAL_RADAR_TYPE_LABELS.get(task.task_type, task.task_type),
        'status': task.status,
        'status_label': task.get_status_display(),
        'stage': task.stage,
        'progress': task.progress,
        'message': task.message,
        'error': task.error,
        'logs': task.logs,
        'created_at': timezone.localtime(task.created_at).strftime('%Y-%m-%d %H:%M:%S'),
        'updated_at': timezone.localtime(task.updated_at).strftime('%Y-%m-%d %H:%M:%S'),
        'result_dir': run_dir.name if run_dir else '',
        'result_links': [
            {'label': item['label'], 'url': f'/social-radar/task/{task.id}/file/{item["relpath"]}'}
            for item in fulltext
        ],
        'target_items': task.target_items,
        'collected_items': task.collected_items,
        'current_scroll': task.current_scroll,
        'max_scrolls': task.max_scrolls,
        'last_new_items': task.last_new_items,
        'fulltext_total': task.fulltext_total,
        'fulltext_processed': task.fulltext_processed,
        'fulltext_hydrated': task.fulltext_hydrated,
        'fulltext_failed': task.fulltext_failed,
        'cancel_requested': task.cancel_requested,
        'expires_at': timezone.localtime(task.expires_at).strftime('%Y-%m-%d %H:%M:%S') if task.expires_at else '',
        'params': sanitize_social_radar_params(task.task_type, task.params),
    }


def _create_social_radar_task(user, task_type: str, params: dict) -> SocialRadarTask:
    task = SocialRadarTask(user=user, task_type=task_type, status=SocialRadarTask.Status.QUEUED)
    task.set_params(params)
    task.stage = '等待执行'
    task.save()
    trigger_social_radar_worker()
    return task


def _get_api_relay_service(service_slug: str):
    service = ApiRelayService.objects.filter(slug=service_slug, is_active=True).first()
    if service_slug == 'tushare' and service is None:
        service = ApiRelayService.objects.create(
            slug='tushare',
            name='Tushare Relay',
            base_url=TUSHARE_RELAY_BASE_URL,
            is_active=True,
            require_api_key=True,
            require_login=False,
            require_manual_approval=True,
            allowed_methods='GET,POST',
            timeout_seconds=60,
            public_path='/tushare/',
            apply_url='/quant/tushare-pro-guide/',
            description='Tushare 数据中继服务。网页登录拿权限的方式已取消，改为由超级管理员发放 API Key。',
            example_paths='/health\n/daily/news\n/daily/000002.SZ/latest',
            note='默认的 Tushare 数据转接服务',
        )
    return service


def _get_user_api_relay_access(user, service):
    if not user or not user.is_authenticated or service is None:
        return None
    access, _ = UserApiRelayAccess.objects.get_or_create(user=user, service=service)
    return access


def _get_tushare_rt_min_tokens() -> list[str]:
    raw = os.getenv('TUSHARE_RT_MIN_TOKENS', DEFAULT_TUSHARE_RT_MIN_TOKENS)
    return [item.strip() for item in re.split(r'[\s,]+', raw) if item.strip()]


def _user_can_access_api_relay(user, service) -> bool:
    if service is None or not service.is_active:
        return False
    if not service.require_login:
        return True
    if not user or not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    if not service.require_manual_approval:
        return True
    access = _get_user_api_relay_access(user, service)
    if not access or not access.is_enabled:
        return False
    if access.expires_at and timezone.now() >= access.expires_at:
        return False
    return True


def _extract_api_key_from_request(request) -> str:
    api_key = (
        request.headers.get('X-API-Key')
        or request.headers.get('X-Api-Key')
        or request.META.get('HTTP_X_API_KEY')
        or ''
    ).strip()
    if api_key:
        return api_key
    auth_header = (request.headers.get('Authorization') or request.META.get('HTTP_AUTHORIZATION') or '').strip()
    if auth_header.lower().startswith('bearer '):
        return auth_header[7:].strip()
    return ''


def _authorize_api_relay_request(request, service):
    access = None
    upstream_user = request.user if request.user.is_authenticated else None
    if service.require_api_key:
        raw_api_key = _extract_api_key_from_request(request)
        if not raw_api_key:
            return None, upstream_user, JsonResponse(
                {
                    'ok': False,
                    'error': 'api_key_required',
                    'message': f'访问 {service.name} 必须在请求头里携带有效的 X-API-Key。',
                    'apply_url': service.apply_url or '/api-relay/',
                },
                status=401,
            )
        access = _get_api_key_access(service, raw_api_key)
        if access is None:
            return None, upstream_user, JsonResponse(
                {
                    'ok': False,
                    'error': 'invalid_api_key',
                    'message': f'你提供的 API Key 无效，或不属于 {service.name}。',
                    'apply_url': service.apply_url or '/api-relay/',
                },
                status=403,
            )
        if not _api_key_can_access_service(access, service):
            return access, upstream_user, JsonResponse(
                {
                    'ok': False,
                    'error': 'permission_denied',
                    'message': f'该 API Key 尚未开通 {service.name} 的访问权限，或权限已过期。',
                    'apply_url': service.apply_url or '/api-relay/',
                },
                status=403,
            )
        upstream_user = access.user
        return access, upstream_user, None
    if service.require_login and not request.user.is_authenticated:
        return None, upstream_user, JsonResponse(
            {
                'ok': False,
                'error': 'login_required',
                'message': '访问该 API 前请先在站内注册并登录。',
                'apply_url': service.apply_url or '/api-relay/',
            },
            status=401,
        )
    if not _user_can_access_api_relay(request.user, service):
        return None, upstream_user, JsonResponse(
            {
                'ok': False,
                'error': 'permission_denied',
                'message': f'你的账号尚未开通 {service.name} 的访问权限。请先注册登录，并等待后台授权。',
                'apply_url': service.apply_url or '/api-relay/',
            },
            status=403,
        )
    return None, upstream_user, None


def _get_api_key_access(service, raw_api_key: str):
    raw_api_key = (raw_api_key or '').strip()
    if not raw_api_key:
        return None
    local_now = timezone.localtime()
    if (
        service.slug == 'tushare'
        and raw_api_key == TEMP_TUSHARE_API_KEY
        and local_now.date() == TEMP_TUSHARE_API_KEY_DATE
        and local_now.time() < TEMP_TUSHARE_API_KEY_CUTOFF
    ):
        return SimpleNamespace(
            user=None,
            service_id=service.id,
            is_enabled=True,
            approved_at=local_now,
            expires_at=None,
        )
    if '.' not in raw_api_key:
        access = (
            UserApiRelayAccess.objects.select_related('user', 'service')
            .filter(service=service, api_key_prefix=raw_api_key)
            .first()
        )
        if not access or not access.api_key_secret_hash:
            return None
        if not check_password(raw_api_key, access.api_key_secret_hash):
            return None
        return access
    prefix, secret = raw_api_key.split('.', 1)
    if not prefix or not secret:
        return None
    access = (
        UserApiRelayAccess.objects.select_related('user', 'service')
        .filter(service=service, api_key_prefix=prefix)
        .first()
    )
    if not access or not access.api_key_secret_hash:
        return None
    if not check_password(secret, access.api_key_secret_hash):
        return None
    return access


def _api_key_can_access_service(access, service) -> bool:
    if not access or access.service_id != service.id:
        return False
    if not service.is_active:
        return False
    if not access.is_enabled:
        return False
    if service.require_manual_approval and not access.approved_at:
        return False
    if access.expires_at and timezone.now() >= access.expires_at:
        return False
    return True


def _relay_path_allowed_for_service(service, relay_path: str) -> tuple[bool, str]:
    normalized = (relay_path or '').lstrip('/')
    if service.slug == 'tushare' and normalized.startswith('minute/'):
        return False, '当前 Tushare relay 的分钟线能力只开放在专门的 `/tushare/minute/*` latest 入口。'
    return True, ''


def _canonicalize_query_params(params) -> list[tuple[str, str]]:
    if hasattr(params, 'lists'):
        pairs = []
        for key, values in params.lists():
            for value in values:
                pairs.append((str(key), '' if value is None else str(value)))
        return sorted(pairs)
    return sorted((str(key), '' if value is None else str(value)) for key, value in params.items())


def _build_tushare_news_cache_key(relay_path: str, params) -> tuple[str, str]:
    normalized_path = (relay_path or '').strip('/')
    canonical_pairs = _canonicalize_query_params(params)
    query_string = urlencode(canonical_pairs, doseq=True)
    digest = hashlib.sha256(f'{normalized_path}?{query_string}'.encode('utf-8')).hexdigest()
    return digest, query_string


def _has_tushare_historical_query_params(params) -> bool:
    normalized_keys = {str(key).strip().lower() for key in (params or {}).keys()}
    return bool(
        normalized_keys.intersection(
            {
                'trade_date', 'start_date', 'end_date', 'ann_date', 'f_ann_date',
                'date', 'start', 'end', 'month', 'quarter', 'period',
            }
        )
    )


def _get_tushare_cache_policy(relay_path: str, params=None) -> tuple[timedelta, timedelta]:
    normalized_path = (relay_path or '').strip('/')
    if normalized_path in {'health', 'status', 'symbols'}:
        return TUSHARE_STATUS_CACHE_TTL, TUSHARE_STATUS_CACHE_PURGE_TTL
    if normalized_path == 'pro/catalog':
        return TUSHARE_CATALOG_CACHE_TTL, TUSHARE_CATALOG_CACHE_PURGE_TTL
    if normalized_path == 'pro/news':
        return TUSHARE_NEWS_CACHE_TTL, TUSHARE_NEWS_CACHE_PURGE_TTL
    if normalized_path == 'pro/express_news':
        return TUSHARE_EXPRESS_NEWS_CACHE_TTL, TUSHARE_EXPRESS_NEWS_CACHE_PURGE_TTL
    if normalized_path == 'pro/analyst_rank':
        return TUSHARE_ANALYST_RANK_CACHE_TTL, TUSHARE_ANALYST_RANK_CACHE_PURGE_TTL
    if normalized_path == 'pro/analyst_detail':
        return TUSHARE_ANALYST_DETAIL_CACHE_TTL, TUSHARE_ANALYST_DETAIL_CACHE_PURGE_TTL
    if normalized_path == 'pro/analyst_history':
        return TUSHARE_ANALYST_HISTORY_CACHE_TTL, TUSHARE_ANALYST_HISTORY_CACHE_PURGE_TTL
    if normalized_path == 'pro/research_report':
        return TUSHARE_RESEARCH_REPORT_CACHE_TTL, TUSHARE_RESEARCH_REPORT_CACHE_PURGE_TTL
    if normalized_path == 'pro/major_news':
        return TUSHARE_MAJOR_NEWS_CACHE_TTL, TUSHARE_MAJOR_NEWS_CACHE_PURGE_TTL
    if normalized_path in {'pro/irm_qa_sh', 'pro/irm_qa_sz'}:
        return TUSHARE_IRM_QA_CACHE_TTL, TUSHARE_IRM_QA_CACHE_PURGE_TTL
    if normalized_path.startswith('daily/') and normalized_path.endswith('/latest'):
        return TUSHARE_DAILY_LATEST_CACHE_TTL, TUSHARE_DAILY_LATEST_CACHE_PURGE_TTL
    if _has_tushare_historical_query_params(params or {}):
        return TUSHARE_GENERIC_HISTORY_CACHE_TTL, TUSHARE_GENERIC_HISTORY_CACHE_PURGE_TTL
    if normalized_path.startswith('pro/') or normalized_path.startswith('daily/'):
        return TUSHARE_GENERIC_CURRENT_CACHE_TTL, TUSHARE_GENERIC_CURRENT_CACHE_PURGE_TTL
    return TUSHARE_NEWS_CACHE_TTL, TUSHARE_NEWS_CACHE_PURGE_TTL


def _get_tushare_replay_cache_windows(relay_path: str, params=None, response_payload: dict | None = None):
    now = timezone.now()
    normalized_path = (relay_path or '').strip('/')
    if normalized_path.startswith('minute/') and normalized_path.endswith('/latest') and response_payload:
        data = response_payload.get('data') or {}
        period_end_text = str(data.get('period_end') or '').strip()
        freq = str((data.get('params') or {}).get('freq') or (params or {}).get('freq') or '').strip().upper()
        if period_end_text and freq in TUSHARE_MINUTE_ALLOWED_FREQS:
            try:
                local_end = datetime.strptime(period_end_text, '%Y-%m-%d %H:%M:%S')
                local_end = timezone.make_aware(local_end, timezone.get_current_timezone())
                next_close = local_end + timedelta(minutes=_get_minute_freq_minutes(freq))
                fresh_until = max(now + timedelta(seconds=5), next_close.astimezone(now.tzinfo))
                return fresh_until, fresh_until + timedelta(days=2)
            except ValueError:
                pass
    active_ttl, purge_ttl = _get_tushare_cache_policy(relay_path, params)
    return now + active_ttl, now + purge_ttl


def _delete_expired_tushare_replay_cache(now=None, relay_path: str = '') -> int:
    now = now or timezone.now()
    queryset = TushareNewsCache.objects.all()
    normalized_path = (relay_path or '').strip('/')
    if normalized_path:
        queryset = queryset.filter(relay_path=normalized_path)
    purge_fallback_ttl = (
        _get_tushare_cache_policy(normalized_path, {})[1]
        if normalized_path else TUSHARE_REPLAY_CACHE_FALLBACK_PURGE_TTL
    )
    deleted, _ = queryset.filter(
        Q(purge_after__lt=now) |
        Q(purge_after__isnull=True, updated_at__lt=now - purge_fallback_ttl)
    ).delete()
    return deleted


def _delete_expired_tushare_replay_leases(now=None) -> int:
    now = now or timezone.now()
    try:
        deleted, _ = TushareReplayLease.objects.filter(lease_until__lt=now).delete()
        return deleted
    except (DBOperationalError, ProgrammingError):
        return 0


@contextmanager
def _acquire_tushare_replay_cache_lock(cache_key: str):
    TUSHARE_REPLAY_LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = TUSHARE_REPLAY_LOCK_DIR / f'{cache_key}.lock'
    with open(lock_path, 'w') as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _maybe_cleanup_tushare_replay_cache(now=None, force: bool = False):
    now = now or timezone.now()
    TUSHARE_REPLAY_LOCK_DIR.mkdir(parents=True, exist_ok=True)
    marker_path = TUSHARE_REPLAY_SWEEP_MARKER
    if not force and marker_path.exists():
        marker_mtime = datetime.fromtimestamp(marker_path.stat().st_mtime, tz=dt_timezone.utc)
        if now - marker_mtime < TUSHARE_REPLAY_CACHE_SWEEP_INTERVAL:
            return
    lock_path = TUSHARE_REPLAY_LOCK_DIR / '.global_cleanup.lock'
    with open(lock_path, 'w') as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        try:
            if not force and marker_path.exists():
                marker_mtime = datetime.fromtimestamp(marker_path.stat().st_mtime, tz=dt_timezone.utc)
                if now - marker_mtime < TUSHARE_REPLAY_CACHE_SWEEP_INTERVAL:
                    return
            _delete_expired_tushare_replay_cache(now=now)
            _delete_expired_tushare_replay_leases(now=now)
            marker_path.touch()
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _get_tushare_news_cache_entry(relay_path: str, params) -> tuple[TushareNewsCache | None, str, str]:
    normalized_path = (relay_path or '').strip('/')
    cache_key, query_string = _build_tushare_news_cache_key(relay_path, params)
    now = timezone.now()
    _maybe_cleanup_tushare_replay_cache(now=now)
    _delete_expired_tushare_replay_cache(now=now, relay_path=normalized_path)
    entry = (
        TushareNewsCache.objects
        .filter(cache_key=cache_key)
        .filter(
            Q(fresh_until__gte=now) |
            Q(fresh_until__isnull=True, updated_at__gte=now - _get_tushare_cache_policy(relay_path, params)[0])
        )
        .first()
    )
    return entry, cache_key, query_string


def _claim_tushare_replay_lease(cache_key: str, relay_path: str, query_string: str, now=None) -> tuple[bool, str]:
    now = now or timezone.now()
    lease_until = now + TUSHARE_REPLAY_LEASE_TTL
    owner_token = secrets.token_hex(16)
    normalized_path = (relay_path or '').strip('/')
    for _ in range(3):
        try:
            with transaction.atomic():
                lease = TushareReplayLease.objects.select_for_update().filter(cache_key=cache_key).first()
                if lease is None:
                    TushareReplayLease.objects.create(
                        cache_key=cache_key,
                        relay_path=normalized_path,
                        query_string=query_string,
                        owner_token=owner_token,
                        lease_until=lease_until,
                    )
                    return True, owner_token
                if lease.lease_until >= now:
                    return False, lease.owner_token
                lease.relay_path = normalized_path
                lease.query_string = query_string
                lease.owner_token = owner_token
                lease.lease_until = lease_until
                lease.save(update_fields=['relay_path', 'query_string', 'owner_token', 'lease_until', 'updated_at'])
                return True, owner_token
        except IntegrityError:
            continue
        except (DBOperationalError, ProgrammingError):
            # 线上如果还没跑租约表迁移，降级为无租约模式，避免直接 500。
            return True, ''
    try:
        lease = TushareReplayLease.objects.filter(cache_key=cache_key).first()
    except (DBOperationalError, ProgrammingError):
        return True, ''
    if lease and lease.lease_until >= now:
        return False, lease.owner_token
    return False, ''


def _release_tushare_replay_lease(cache_key: str, owner_token: str):
    if not cache_key or not owner_token:
        return
    try:
        TushareReplayLease.objects.filter(cache_key=cache_key, owner_token=owner_token).delete()
    except (DBOperationalError, ProgrammingError):
        return


def _wait_for_tushare_replay_cache_fill(relay_path: str, params, cache_key: str):
    deadline = timezone.now() + TUSHARE_REPLAY_FOLLOWER_WAIT_TIMEOUT
    while timezone.now() < deadline:
        cache_entry, _, _ = _get_tushare_news_cache_entry(relay_path, params)
        if cache_entry is not None:
            return cache_entry
        try:
            lease = TushareReplayLease.objects.filter(cache_key=cache_key).first()
        except (DBOperationalError, ProgrammingError):
            return None
        now = timezone.now()
        if lease is None or lease.lease_until < now:
            return None
        time_module.sleep(TUSHARE_REPLAY_FOLLOWER_POLL_INTERVAL_SECONDS)
    return None


def _build_cached_tushare_news_response(entry: TushareNewsCache, upstream_base: str):
    response = HttpResponse(
        entry.response_body,
        status=entry.status_code,
        content_type=entry.content_type or 'application/json',
    )
    response['X-Api-Relay-Service'] = 'tushare'
    response['X-Api-Relay-Upstream'] = upstream_base
    response['X-Api-Relay-Cache'] = 'HIT'
    response['X-Tushare-News-Cache-Updated-At'] = timezone.localtime(entry.updated_at).strftime('%Y-%m-%d %H:%M:%S')
    return response


def _store_tushare_replay_cache(
    *,
    cache_key: str,
    relay_path: str,
    query_string: str,
    response_body: str,
    status_code: int,
    content_type: str,
    params=None,
    response_payload: dict | None = None,
):
    fresh_until, purge_after = _get_tushare_replay_cache_windows(relay_path, params=params, response_payload=response_payload)
    TushareNewsCache.objects.update_or_create(
        cache_key=cache_key,
        defaults={
            'relay_path': (relay_path or '').strip('/'),
            'query_string': query_string,
            'response_body': response_body,
            'status_code': status_code,
            'content_type': (content_type or 'application/json')[:120],
            'fresh_until': fresh_until,
            'purge_after': purge_after,
        },
    )


def _is_tushare_local_proxy(relay_path: str) -> bool:
    return (relay_path or '').strip('/') in {
        'pro/express_news',
        'pro/analyst_rank',
        'pro/analyst_detail',
        'pro/analyst_history',
        'pro/research_report',
    }


def _is_tushare_local_news_proxy(relay_path: str) -> bool:
    return (relay_path or '').strip('/') == 'pro/express_news'


def _is_tushare_express_news_realtime_only(relay_path: str, params: dict) -> bool:
    """express_news 统一走本地分支；历史查询当前直接返回明确错误说明。"""
    if not _is_tushare_local_news_proxy(relay_path):
        return False
    return True


def _normalize_tushare_express_news_scope(scope: str) -> str:
    raw = (scope or '').strip().lower()
    if raw in {'important', 'focus', 'key', '重点'}:
        return '重点'
    return '全部'


def _parse_tushare_fields(fields: str, default_fields: list[str]) -> list[str]:
    raw_fields = [item.strip() for item in str(fields or '').split(',') if item.strip()]
    if not raw_fields:
        return list(default_fields)
    result = []
    seen = set()
    for field in raw_fields:
        if field not in seen:
            seen.add(field)
            result.append(field)
    return result


def _serialize_tushare_express_news_item(item: dict, fields: list[str]) -> dict:
    return {field: item.get(field, '') for field in fields}


def _normalize_tushare_scalar(value):
    if value is None:
        return ''
    if hasattr(value, 'item'):
        try:
            value = value.item()
        except Exception:
            pass
    if isinstance(value, float):
        try:
            if math.isnan(value):
                return ''
        except Exception:
            pass
    if str(type(value)).startswith("<class 'pandas.") and str(value) == 'NaT':
        return ''
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _serialize_tushare_df_records(df, fields: list[str] | None = None) -> list[dict]:
    records = []
    for row in df.to_dict('records'):
        item = {str(key): _normalize_tushare_scalar(value) for key, value in row.items()}
        if fields:
            item = {field: item.get(field, '') for field in fields}
        records.append(item)
    return records


def _fetch_tushare_express_news(params: dict) -> dict:
    try:
        limit = int(params.get('limit') or 50)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 100))
    requested_fields = _parse_tushare_fields(
        params.get('fields') or '',
        ['title', 'content', 'datetime', 'src'],
    )
    news_df = ak.stock_info_global_cls(symbol=scope)
    records = []
    for row in news_df.to_dict('records')[:limit]:
        pub_date = row.get('发布日期')
        pub_time = row.get('发布时间')
        dt_text = ''
        if pub_date and pub_time:
            dt_text = f'{pub_date} {pub_time}'
        elif pub_date:
            dt_text = str(pub_date)
        elif pub_time:
            dt_text = str(pub_time)
        item = {
            'title': str(row.get('标题') or '').strip(),
            'content': str(row.get('内容') or '').strip(),
            'datetime': dt_text.strip(),
            'src': 'express',
        }
        records.append(_serialize_tushare_express_news_item(item, requested_fields))
    return {
        'api_name': 'express_news',
        'code': 0,
        'msg': 'ok',
        'params': {
            'scope': 'important' if scope == '重点' else 'all',
            'limit': limit,
            'fields': ','.join(requested_fields),
        },
        'count': len(records),
        'data': records,
    }


def _fetch_tushare_analyst_rank(params: dict) -> dict:
    year = str(params.get('year') or timezone.localdate().year).strip()
    requested_fields = _parse_tushare_fields(
        params.get('fields') or '',
        ['分析师名称', '分析师单位', '年度指数', '12个月收益率', '分析师ID', '行业', '更新日期', '年度'],
    )
    try:
        limit = int(params.get('limit') or 50)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 200))
    df = ak.stock_analyst_rank_em(year=year)
    records = _serialize_tushare_df_records(df.head(limit), requested_fields)
    return {
        'api_name': 'analyst_rank',
        'code': 0,
        'msg': 'ok',
        'params': {
            'year': year,
            'limit': limit,
            'fields': ','.join(requested_fields),
        },
        'count': len(records),
        'data': records,
    }


def _fetch_tushare_analyst_detail(params: dict) -> dict:
    analyst_id = str(params.get('analyst_id') or '').strip()
    if not analyst_id:
        raise ValueError('缺少 analyst_id 参数')
    indicator = str(params.get('indicator') or '最新跟踪成分股').strip() or '最新跟踪成分股'
    requested_fields = _parse_tushare_fields(
        params.get('fields') or '',
        ['股票代码', '股票名称', '调入日期', '最新评级日期', '当前评级名称', '最新价格', '阶段涨跌幅'],
    )
    try:
        limit = int(params.get('limit') or 50)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 200))
    df = ak.stock_analyst_detail_em(analyst_id=analyst_id, indicator=indicator)
    records = _serialize_tushare_df_records(df.head(limit), requested_fields)
    return {
        'api_name': 'analyst_detail',
        'code': 0,
        'msg': 'ok',
        'params': {
            'analyst_id': analyst_id,
            'indicator': indicator,
            'limit': limit,
            'fields': ','.join(requested_fields),
        },
        'count': len(records),
        'data': records,
    }


def _normalize_tushare_analyst_history_indicator(indicator: str) -> str:
    raw = str(indicator or '').strip().lower()
    if raw in {'', 'history_hold', 'history_holding', 'holding', '成分股', '历史成分股', '历史跟踪成分股'}:
        return '历史跟踪成分股'
    if raw in {'history_index', 'index', '历史指数'}:
        return '历史指数'
    raise ValueError('indicator 只支持 历史跟踪成分股 或 历史指数')


def _fetch_tushare_analyst_history(params: dict) -> dict:
    analyst_id = str(params.get('analyst_id') or '').strip()
    if not analyst_id:
        raise ValueError('缺少 analyst_id 参数')
    indicator = _normalize_tushare_analyst_history_indicator(params.get('indicator') or '')
    default_fields = (
        ['股票代码', '股票名称', '调入日期', '调出日期', '调入时评级名称', '调出原因', '累计涨跌幅']
        if indicator == '历史跟踪成分股'
        else ['date', 'value']
    )
    requested_fields = _parse_tushare_fields(params.get('fields') or '', default_fields)
    try:
        limit = int(params.get('limit') or 100)
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 500))
    df = ak.stock_analyst_detail_em(analyst_id=analyst_id, indicator=indicator)
    records = _serialize_tushare_df_records(df.head(limit), requested_fields)
    return {
        'api_name': 'analyst_history',
        'code': 0,
        'msg': 'ok',
        'params': {
            'analyst_id': analyst_id,
            'indicator': indicator,
            'limit': limit,
            'fields': ','.join(requested_fields),
        },
        'count': len(records),
        'data': records,
    }


def _fetch_tushare_research_report(params: dict) -> dict:
    symbol = str(params.get('symbol') or params.get('ts_code') or '').strip()
    if not symbol:
        raise ValueError('缺少 symbol 或 ts_code 参数')
    digits = re.sub(r'[^0-9]', '', symbol)
    if len(digits) != 6:
        raise ValueError('symbol 或 ts_code 需要 6 位股票代码')
    requested_fields = _parse_tushare_fields(
        params.get('fields') or '',
        ['股票代码', '股票简称', '报告名称', '东财评级', '机构', '日期', '报告PDF链接'],
    )
    try:
        limit = int(params.get('limit') or 50)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 200))
    df = ak.stock_research_report_em(symbol=digits)
    records = _serialize_tushare_df_records(df.head(limit), requested_fields)
    return {
        'api_name': 'research_report',
        'code': 0,
        'msg': 'ok',
        'params': {
            'symbol': digits,
            'limit': limit,
            'fields': ','.join(requested_fields),
        },
        'count': len(records),
        'data': records,
    }


def _fetch_tushare_local_proxy_payload(relay_path: str, params: dict) -> dict:
    normalized_path = (relay_path or '').strip('/')
    if normalized_path == 'pro/express_news':
        return _fetch_tushare_express_news(params)
    if normalized_path == 'pro/analyst_rank':
        return _fetch_tushare_analyst_rank(params)
    if normalized_path == 'pro/analyst_detail':
        return _fetch_tushare_analyst_detail(params)
    if normalized_path == 'pro/analyst_history':
        return _fetch_tushare_analyst_history(params)
    if normalized_path == 'pro/research_report':
        return _fetch_tushare_research_report(params)
    raise ValueError('unsupported local tushare proxy path')


def _build_tushare_local_news_response(payload: dict, service, cache_key: str = '', cache_query_string: str = '', relay_path: str = '', cached: bool = False):
    upstream_base = service.base_url.rstrip('/')
    response = JsonResponse(payload, json_dumps_params={'ensure_ascii': False})
    response['X-Api-Relay-Service'] = service.slug
    response['X-Api-Relay-Upstream'] = upstream_base
    response['X-Api-Relay-Cache'] = 'HIT' if cached else 'MISS'
    if not cached and cache_key:
        _store_tushare_replay_cache(
            cache_key=cache_key,
            relay_path=relay_path,
            query_string=cache_query_string,
            response_body=json.dumps(payload, ensure_ascii=False),
            status_code=200,
            content_type='application/json',
            params=dict(payload.get('params') or {}),
            response_payload=payload,
        )
    return response


def _should_use_tushare_news_cache(service, request, relay_path: str) -> bool:
    return (
        service.slug == 'tushare'
        and request.method.upper() == 'GET'
        and bool((relay_path or '').strip('/'))
    )


def _build_tushare_rt_min_record(payload: dict) -> dict:
    data = payload.get('data') or {}
    fields = data.get('fields') or []
    items = data.get('items') or []
    if not fields or not items:
        return {}
    return {field: value for field, value in zip(fields, items[0])}


def _parse_tushare_rt_min_start(record: dict) -> datetime | None:
    raw = str((record or {}).get('time') or '').strip()
    if not raw:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y%m%d %H:%M:%S', '%Y%m%d%H%M%S'):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _get_tushare_rt_min_period_meta(record: dict, freq: str) -> dict:
    start_dt = _parse_tushare_rt_min_start(record)
    freq_minutes = 30 if (freq or '').upper() == '30MIN' else 1
    if start_dt is None:
        return {
            'bar_semantic': 'latest_completed',
            'is_complete': False,
            'period_start': None,
            'period_end': None,
        }
    end_dt = start_dt + timedelta(minutes=freq_minutes)
    now = timezone.localtime().replace(tzinfo=None)
    return {
        'bar_semantic': 'latest_completed',
        'is_complete': now >= end_dt,
        'period_start': start_dt.strftime('%Y-%m-%d %H:%M:%S'),
        'period_end': end_dt.strftime('%Y-%m-%d %H:%M:%S'),
    }


def _get_minute_freq_minutes(freq: str) -> int:
    return TUSHARE_MINUTE_ALLOWED_FREQS.get((freq or '').upper(), 0)


def _normalize_a_share_minute_symbol(symbol: str) -> str:
    raw = (symbol or '').strip().upper()
    if not TUSHARE_A_SHARE_TS_CODE_RE.match(raw):
        return ''
    code, market = raw.split('.', 1)
    return f'{market.lower()}{code}'


@lru_cache(maxsize=1)
def _get_futures_minute_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    try:
        display_df = ak.futures_display_main_sina()
    except Exception:
        display_df = None
    if display_df is not None:
        for row in display_df.to_dict('records'):
            symbol = str(row.get('symbol') or '').strip().upper()
            name = str(row.get('name') or '').strip()
            if not symbol:
                continue
            aliases[symbol] = symbol
            if symbol.endswith('0'):
                aliases[symbol[:-1]] = symbol
            if name:
                aliases[name] = symbol
                aliases[name.replace('连续', '').strip()] = symbol
    try:
        mark_df = ak.futures_symbol_mark()
    except Exception:
        mark_df = None
    if mark_df is not None:
        for row in mark_df.to_dict('records'):
            name = str(row.get('symbol') or '').strip()
            if not name:
                continue
            existing = aliases.get(name)
            if existing:
                aliases[name] = existing
    return aliases


def _normalize_futures_minute_symbol(symbol: str) -> str:
    raw = (symbol or '').strip()
    if not raw:
        return ''
    aliases = _get_futures_minute_aliases()
    alias_hit = aliases.get(raw) or aliases.get(raw.upper())
    if alias_hit:
        return alias_hit
    upper_raw = raw.upper()
    match = TUSHARE_FUTURES_SYMBOL_WITH_EXCHANGE_RE.match(upper_raw)
    if not match:
        return ''
    core = match.group(1)
    if core.isalpha():
        return aliases.get(core, f'{core}0')
    return aliases.get(core, core)


def _parse_sina_a_share_jsonp(text: str) -> list:
    raw = (text or '').strip()
    if '=(' not in raw:
        return []
    payload = raw.split('=(', 1)[1].rsplit(');', 1)[0]
    data = json.loads(payload)
    return data if isinstance(data, list) else []


def _parse_sina_futures_jsonp(text: str) -> list:
    raw = (text or '').strip()
    if '=(' not in raw:
        return []
    payload = raw.split('=(', 1)[1].rsplit(');', 1)[0]
    data = json.loads(payload)
    return data if isinstance(data, list) else []


def _build_a_share_minute_record(item: dict, symbol: str, freq: str) -> dict:
    record = {
        'datetime': item.get('day') or item.get('datetime') or '',
        'open': item.get('open') or 0,
        'high': item.get('high') or 0,
        'low': item.get('low') or 0,
        'close': item.get('close') or 0,
        'volume': item.get('volume') or 0,
        'amount': item.get('amount') or 0,
        'ts_code': symbol,
        'freq': freq,
    }
    for key in ['open', 'high', 'low', 'close', 'amount']:
        record[key] = float(record[key])
    record['volume'] = int(float(record['volume']))
    return record


def _build_futures_minute_record(item, symbol: str, freq: str) -> dict:
    if isinstance(item, dict):
        record = {
            'datetime': item.get('d') or item.get('datetime') or '',
            'open': item.get('o') or item.get('open') or 0,
            'high': item.get('h') or item.get('high') or 0,
            'low': item.get('l') or item.get('low') or 0,
            'close': item.get('c') or item.get('close') or 0,
            'volume': item.get('v') or item.get('volume') or 0,
            'hold': item.get('p') or item.get('hold') or 0,
        }
    else:
        fields = ['datetime', 'open', 'high', 'low', 'close', 'volume', 'hold']
        record = {field: value for field, value in zip(fields, item)}
    for key in ['open', 'high', 'low', 'close']:
        record[key] = float(record[key])
    for key in ['volume', 'hold']:
        record[key] = int(float(record[key]))
    record['symbol'] = symbol
    record['freq'] = freq
    return record


def _get_futures_minute_period_meta(record: dict, freq: str) -> dict:
    raw = str((record or {}).get('datetime') or '').strip()
    freq_minutes = _get_minute_freq_minutes(freq)
    if not raw or not freq_minutes:
        return {
            'bar_semantic': 'latest_completed',
            'is_complete': False,
            'period_start': None,
            'period_end': None,
        }
    end_dt = datetime.strptime(raw, '%Y-%m-%d %H:%M:%S')
    start_dt = end_dt - timedelta(minutes=freq_minutes)
    now = timezone.localtime().replace(tzinfo=None)
    return {
        'bar_semantic': 'latest_completed',
        'is_complete': now >= end_dt,
        'period_start': start_dt.strftime('%Y-%m-%d %H:%M:%S'),
        'period_end': end_dt.strftime('%Y-%m-%d %H:%M:%S'),
    }


def _get_a_share_minute_period_meta(record: dict, freq: str) -> dict:
    raw = str((record or {}).get('datetime') or '').strip()
    freq_minutes = _get_minute_freq_minutes(freq)
    if not raw or not freq_minutes:
        return {
            'bar_semantic': 'latest_completed',
            'is_complete': False,
            'period_start': None,
            'period_end': None,
        }
    end_dt = datetime.strptime(raw, '%Y-%m-%d %H:%M:%S')
    start_dt = end_dt - timedelta(minutes=freq_minutes)
    now = timezone.localtime().replace(tzinfo=None)
    return {
        'bar_semantic': 'latest_completed',
        'is_complete': now >= end_dt,
        'period_start': start_dt.strftime('%Y-%m-%d %H:%M:%S'),
        'period_end': end_dt.strftime('%Y-%m-%d %H:%M:%S'),
    }


def _fetch_tushare_a_share_minute_latest(symbol: str, freq: str) -> dict:
    normalized_symbol = _normalize_a_share_minute_symbol(symbol)
    period = str(TUSHARE_A_SHARE_MIN_ALLOWED_FREQS.get(freq, 0))
    upstream = TUSHARE_A_SHARE_MIN_HTTP_SESSION.get(
        TUSHARE_A_SHARE_MIN_UPSTREAM_URL,
        params={'symbol': normalized_symbol, 'scale': period, 'ma': 'no', 'datalen': '1970'},
        timeout=(5, 20),
    )
    upstream.raise_for_status()
    items = _parse_sina_a_share_jsonp(upstream.text)
    if not items:
        return {}
    return _build_a_share_minute_record(items[-1], symbol, freq)


def _fetch_tushare_futures_minute_latest(symbol: str, freq: str) -> dict:
    period = str(_get_minute_freq_minutes(freq))
    upstream = TUSHARE_FUTURES_MIN_HTTP_SESSION.get(
        TUSHARE_FUTURES_MIN_UPSTREAM_URL,
        params={'symbol': symbol, 'type': period},
        timeout=(5, 20),
    )
    upstream.raise_for_status()
    items = _parse_sina_futures_jsonp(upstream.text)
    if not items:
        return {}
    return _build_futures_minute_record(items[-1], symbol, freq)


def _fetch_tushare_rt_min_with_fallback(ts_code: str, freq: str) -> tuple[dict, int]:
    last_payload = None
    last_error = ''
    tokens = _get_tushare_rt_min_tokens()
    for index, token in enumerate(tokens):
        try:
            upstream = TUSHARE_DIRECT_HTTP_SESSION.post(
                TUSHARE_RT_MIN_UPSTREAM_URL,
                json={
                    'api_name': 'rt_min',
                    'token': token,
                    'params': {'ts_code': ts_code, 'freq': freq},
                },
                timeout=(5, 20),
            )
            payload = upstream.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = str(exc)
            continue
        last_payload = payload if isinstance(payload, dict) else {'code': 502, 'msg': '上游返回格式异常'}
        if last_payload.get('code') == 0:
            return last_payload, index
        last_error = str(last_payload.get('msg') or f'upstream_code_{last_payload.get("code")}')
    if last_payload is not None:
        return last_payload, -1
    return {'code': 503, 'msg': f'rt_min 上游暂时不可用: {last_error or "unknown_error"}'}, -1


def _tushare_minute_proxy(request, service, relay_path: str):
    normalized = (relay_path or '').strip('/')
    if request.method != 'GET':
        return JsonResponse(
            {'ok': False, 'error': 'method_not_allowed', 'message': '分钟 replay 目前只支持 GET。'},
            status=405,
        )
    parts = normalized.split('/')
    if len(parts) != 3 or parts[0] != 'minute' or parts[2] != 'latest':
        return JsonResponse(
            {
                'ok': False,
                'error': 'path_not_supported',
                'message': '分钟 replay 目前只支持 `/tushare/minute/<symbol>/latest?freq=<1MIN|5MIN|15MIN|30MIN|60MIN>`。',
            },
            status=400,
        )
    symbol = parts[1].upper()
    freq = (request.GET.get('freq') or TUSHARE_RT_MIN_ALLOWED_FREQ).strip().upper()
    if freq not in TUSHARE_MINUTE_ALLOWED_FREQS:
        return JsonResponse(
            {
                'ok': False,
                'error': 'freq_not_supported',
                'message': '当前分钟 replay 只支持 `1MIN`、`5MIN`、`15MIN`、`30MIN`、`60MIN`。',
            },
            status=400,
        )
    _, _, auth_error = _authorize_api_relay_request(request, service)
    if auth_error is not None:
        return auth_error
    upstream_base = service.base_url.rstrip('/')
    upstream_params = {'freq': freq}
    cache_entry, cache_key, cache_query_string = _get_tushare_news_cache_entry(relay_path, upstream_params)
    if cache_entry is not None:
        return _build_cached_tushare_news_response(cache_entry, upstream_base)
    is_leader, lease_token = _claim_tushare_replay_lease(cache_key, relay_path, cache_query_string)
    if not is_leader:
        cache_entry = _wait_for_tushare_replay_cache_fill(relay_path, upstream_params, cache_key)
        if cache_entry is not None:
            return _build_cached_tushare_news_response(cache_entry, upstream_base)
        return JsonResponse(
            {'code': 503, 'msg': '分钟 replay 正在刷新中，请稍后重试。'},
            status=503,
        )

    try:
        if TUSHARE_A_SHARE_TS_CODE_RE.match(symbol):
            if freq not in TUSHARE_A_SHARE_MIN_ALLOWED_FREQS:
                return JsonResponse(
                    {
                        'ok': False,
                        'error': 'freq_not_supported',
                        'message': 'A 股分钟 replay 当前支持 `1MIN`、`5MIN`、`15MIN`、`30MIN`、`60MIN latest`。',
                    },
                    status=400,
                )
            try:
                record = _fetch_tushare_a_share_minute_latest(symbol, freq)
            except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
                return JsonResponse(
                    {'code': 503, 'msg': f'分钟 replay 上游暂时不可用: {exc}'},
                    status=503,
                )
            if not record:
                return JsonResponse(
                    {'code': 503, 'msg': '分钟 replay 上游暂时无可用数据。'},
                    status=503,
                )
            period_meta = _get_a_share_minute_period_meta(record, freq)
            if record and not period_meta.get('is_complete'):
                response = JsonResponse(
                    {
                        'code': 503,
                        'msg': '当前周期尚未闭合，replay 默认只返回最近一根已闭合周期结果。',
                        'data': {
                            'api_name': 'minute_latest',
                            'record': record,
                            'params': {'ts_code': symbol, 'freq': freq},
                            **period_meta,
                        },
                    },
                    status=503,
                )
                response['X-Api-Relay-Service'] = service.slug
                response['X-Tushare-Relay-Mode'] = f'a-share-{freq.lower()}-latest-completed'
                return response
            response_payload = {
                'code': 0,
                'msg': '',
                'data': {
                    'api_name': 'minute_latest',
                    'record': record,
                    'params': {'ts_code': symbol, 'freq': freq},
                    'path': f'/tushare/minute/{symbol}/latest?freq={freq}',
                    **period_meta,
                },
            }
            response = JsonResponse(response_payload, status=200)
            response['X-Api-Relay-Service'] = service.slug
            response['X-Api-Relay-Cache'] = 'MISS'
            response['X-Tushare-Relay-Mode'] = f'a-share-{freq.lower()}-latest-completed'
            _store_tushare_replay_cache(
                cache_key=cache_key,
                relay_path=relay_path,
                query_string=cache_query_string,
                response_body=json.dumps(response_payload, ensure_ascii=False),
                status_code=200,
                content_type='application/json',
                params=upstream_params,
                response_payload=response_payload,
            )
            return response

        normalized_futures_symbol = _normalize_futures_minute_symbol(symbol)
        if not normalized_futures_symbol:
            response = JsonResponse(
                {
                    'ok': False,
                    'error': 'symbol_not_supported',
                    'message': '当前分钟 replay 只支持 A 股代码或国内期货符号，例如 `000001.SZ`、`RB`、`RB0`、`IF0`、`CU2605.SHFE`。',
                },
                status=400,
            )
            return response
        if freq not in TUSHARE_FUTURES_MIN_ALLOWED_FREQS:
            return JsonResponse(
                {
                    'ok': False,
                    'error': 'freq_not_supported',
                    'message': '国内期货分钟 replay 当前支持 `1MIN`、`5MIN`、`15MIN`、`30MIN latest`。',
                },
                status=400,
            )

        try:
            record = _fetch_tushare_futures_minute_latest(normalized_futures_symbol, freq)
        except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
            return JsonResponse(
                {'code': 503, 'msg': f'分钟 replay 上游暂时不可用: {exc}'},
                status=503,
            )
        if not record:
            return JsonResponse(
                {'code': 503, 'msg': '分钟 replay 上游暂时无可用数据。'},
                status=503,
            )
        record['symbol'] = symbol
        period_meta = _get_futures_minute_period_meta(record, freq)
        response_payload = {
            'code': 0,
            'msg': '',
            'data': {
                'api_name': 'minute_latest',
                'record': record,
                'params': {'symbol': symbol, 'freq': freq},
                'path': f'/tushare/minute/{symbol}/latest?freq={freq}',
                **period_meta,
            },
        }
        response = JsonResponse(response_payload, status=200)
        response['X-Api-Relay-Service'] = service.slug
        response['X-Api-Relay-Cache'] = 'MISS'
        response['X-Tushare-Relay-Mode'] = f'futures-{freq.lower()}-latest-completed'
        _store_tushare_replay_cache(
            cache_key=cache_key,
            relay_path=relay_path,
            query_string=cache_query_string,
            response_body=json.dumps(response_payload, ensure_ascii=False),
            status_code=200,
            content_type='application/json',
            params=upstream_params,
            response_payload=response_payload,
        )
        return response
    finally:
        _release_tushare_replay_lease(cache_key, lease_token)


def _build_api_relay_service_cards(request):
    services = list(ApiRelayService.objects.filter(is_active=True).order_by('name', 'slug'))
    access_map = {}
    if request.user.is_authenticated:
        access_map = {
            access.service_id: access
            for access in UserApiRelayAccess.objects.select_related('service').filter(user=request.user)
        }
    cards = []
    for service in services:
        access = access_map.get(service.id)
        approved = _user_can_access_api_relay(request.user, service)
        example_lines = [line.strip() for line in (service.example_paths or '').splitlines() if line.strip()]
        example_urls = [
            request.build_absolute_uri(f'{service.public_url_path.rstrip("/")}{line if line.startswith("/") else "/" + line}')
            for line in example_lines
        ]
        cards.append(
            {
                'service': service,
                'access': access,
                'approved': approved,
                'status_label': '已开通' if approved else ('待管理员授权' if request.user.is_authenticated else '需先登录'),
                'status_color': '#166534' if approved else '#9a3412',
                'public_url_path': service.public_url_path,
                'absolute_root_url': request.build_absolute_uri(service.public_url_path),
                'example_urls': example_urls,
            }
        )
    return cards

COLUMN_PAGES = {
    'free_resources': {
        'title': '免费 AI 资源大全',
        'path': '/free/search/',
        'template': 'tools/free_resources.html',
    },
    'psychology_column': {
        'title': '心理学专栏',
        'path': '/psychology/',
        'template': 'tools/psychology_column.html',
    },
    'openclaw_column': {
        'title': 'OpenClaw 专栏',
        'path': '/openclaw/',
        'template': 'tools/openclaw_column.html',
    },
    'algorithm_geek_column': {
        'title': 'AI算法专栏',
        'path': '/algorithm-geek/',
        'template': 'tools/algorithm_geek_column.html',
    },
    'quant_column': {
        'title': '量化资源专栏',
        'path': '/quant/',
        'template': 'tools/quant_column.html',
    },
}

FREE_RESOURCE_GROUPS = [
    {
        'key': 'models',
        'title': '免费模型',
        'description': '直接可用的大模型、聊天入口和免费顶级模型平台。',
        'slugs': [
            'cto-new',
            'anyrouter-top-checkin',
            'agentrouter-225-credit',
            'google-gemini-enterprise免费版',
            'google-gemini-25-pro-免费200万token',
            'happycapy',
            'arenaai无限用opus-46',
        ],
    },
    {
        'key': 'api',
        'title': '免费 API',
        'description': '适合接程序、工作流和自动化调用的免费或送额度 API。',
        'slugs': [
            '无限codex-api',
            'gemai-api-public',
            '免费大模型api平台大全',
            'npc-api公益站',
            'laozhangai-china-direct',
            'puterjs-free-claude',
            'google-vertex-ai-300-credit',
            'aws-bedrock-free-tier',
        ],
    },
    {
        'key': 'openclaw',
        'title': '免费 OpenClaw',
        'description': '和 OpenClaw 相关的一键启动、部署、技能和配套入口。',
        'slugs': [
            '免费openclaw一键启动-腾讯cnb',
            'autoglm沉思版',
        ],
    },
    {
        'key': 'coding',
        'title': '免费编程',
        'description': '免费 AI IDE、代码助手和开发协作工具。',
        'slugs': [
            'opencode-glm47-free',
            'trae',
            'kiro',
            'open-lovable-react-builder',
            '百度秒哒',
        ],
    },
    {
        'key': 'media',
        'title': '免费图像视频',
        'description': '免费可用的图像、视频、语音和 AIGC 创作工具。',
        'slugs': [
            '呜哩ai-一站式aigc创意平台',
            '腾讯混元3d-照片变游戏角色',
            'flux1-kontext-史上最强人物一致性',
            'qwen3-tts-space',
        ],
    },
    {
        'key': 'compute',
        'title': '免费算力',
        'description': '免费 GPU、云开发环境和算力相关入口。',
        'slugs': [
            '腾讯云原生开发cnb',
            'kaggle',
            'google-colab',
            '百度ai-studio',
            '阿里天池实验室',
        ],
    },
]


def _track_column_page(page_key):
    page_config = COLUMN_PAGES[page_key]
    column, _ = ColumnPage.objects.get_or_create(
        page_key=page_key,
        defaults={
            'title': page_config['title'],
            'path': page_config['path'],
            'view_count': 0,
        }
    )

    column.title = page_config['title']
    column.path = page_config['path']
    column.view_count += 1
    column.save(update_fields=['title', 'path', 'view_count', 'updated_at'])

    daily_view, _ = ColumnDailyView.objects.get_or_create(
        column=column,
        date=timezone.localdate(),
        defaults={'count': 0}
    )
    daily_view.count += 1
    daily_view.save(update_fields=['count', 'updated_at'])

    return {
        'page_key': page_key,
        'title': column.title,
        'path': column.path,
        'total_views': column.view_count,
        'today_views': daily_view.count,
    }


def _get_column_leaderboard(days=7):
    start_date = timezone.localdate() - timedelta(days=days - 1)
    tracked_columns = {
        item.page_key: item
        for item in (
            ColumnPage.objects
            .annotate(
                week_views=Sum(
                    'daily_views__count',
                    filter=Q(daily_views__date__gte=start_date)
                )
            )
            .order_by('-week_views', '-view_count', 'title')
        )
    }

    leaderboard = []
    for page_key, config in COLUMN_PAGES.items():
        tracked = tracked_columns.get(page_key)
        leaderboard.append({
            'page_key': page_key,
            'title': config['title'],
            'path': config['path'],
            'url_name': page_key,
            'total_views': tracked.view_count if tracked else 0,
            'week_views': tracked.week_views if tracked and tracked.week_views else 0,
        })

    leaderboard.sort(key=lambda item: (-item['week_views'], -item['total_views'], item['title']))
    return leaderboard, start_date


def _build_free_resource_index():
    ordered_groups = []
    for group in FREE_RESOURCE_GROUPS:
        slug_order = group['slugs']
        tools_map = {
            tool.slug: tool
            for tool in Tool.objects.filter(
                is_published=True,
                slug__in=slug_order,
            ).select_related('category')
        }
        tools = [tools_map[slug] for slug in slug_order if slug in tools_map]
        if tools:
            ordered_groups.append({
                'key': group['key'],
                'title': group['title'],
                'description': group['description'],
                'tools': tools,
            })
    return ordered_groups


def free_resources(request):
    """免费AI资源聚合页"""
    missing_free_tool_slugs = [
        'cto-new',
        'anyrouter-top-checkin',
        'agentrouter-225-credit',
        'gemai-api-public',
        '无限codex-api',
        'google-gemini-enterprise免费版',
        '免费大模型api平台大全',
        '免费openclaw一键启动-腾讯cnb',
        'npc-api公益站',
        'happycapy',
        'kiro',
        'arenaai无限用opus-46',
        'laozhangai-china-direct',
        'puterjs-free-claude',
        'opencode-glm47-free',
    ]
    free_tools_map = Tool.objects.filter(
        is_published=True,
        slug__in=missing_free_tool_slugs,
    ).select_related('category')
    free_tools_map = {tool.slug: tool for tool in free_tools_map}
    missing_free_tools = [
        free_tools_map[slug]
        for slug in missing_free_tool_slugs
        if slug in free_tools_map
    ]
    return render(
        request,
        COLUMN_PAGES['free_resources']['template'],
        {
            'column_stats': _track_column_page('free_resources'),
            'missing_free_tools': missing_free_tools,
            'free_resource_groups': _build_free_resource_index(),
        }
    )


def psychology_column(request):
    """心理学专栏列表页"""
    return render(
        request,
        COLUMN_PAGES['psychology_column']['template'],
        {'column_stats': _track_column_page('psychology_column')}
    )


def openclaw_column(request):
    """OpenClaw 专栏列表页"""
    return render(
        request,
        COLUMN_PAGES['openclaw_column']['template'],
        {'column_stats': _track_column_page('openclaw_column')}
    )


def algorithm_geek_column(request):
    """算法极客专栏列表页"""
    return render(
        request,
        COLUMN_PAGES['algorithm_geek_column']['template'],
        {'column_stats': _track_column_page('algorithm_geek_column')}
    )


def quant_column(request):
    """量化资源专栏列表页"""
    return render(
        request,
        COLUMN_PAGES['quant_column']['template'],
        {'column_stats': _track_column_page('quant_column')}
    )


def psychology_article_evolution(request):
    """心理学专栏文章：进化不靠意志力"""
    return render(request, 'tools/psychology_article_evolution.html')


def psychology_sleep_category(request):
    """心理学专栏：睡眠分类"""
    return render(request, 'tools/psychology_sleep_category.html')


def psychology_article_sleep(request):
    """心理学专栏文章：睡眠困扰终结指南"""
    return render(request, 'tools/psychology_article_sleep.html')


def psychology_article_zebra_stress(request):
    """心理学专栏文章：斑马与压力机制"""
    return render(request, 'tools/psychology_article_zebra_stress.html')


@ensure_csrf_cookie
def quant_article_tardis(request):
    """量化资源专栏文章：TARDIS 数据指南"""
    context = {
        'tardis_admin_logged_in': _is_tardis_superadmin(request),
        'tardis_rag_entries': TardisRagEntry.objects.order_by('sort_order', '-updated_at', '-id')[:50],
        'tardis_superadmin_username': TARDIS_SUPERADMIN_USERNAME,
    }
    return render(request, 'tools/quant_article_tardis.html', context)


def quant_article_tardis_rag(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'method_not_allowed'}, status=405)
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'invalid_json', 'message': '请求体需要是合法 JSON。'}, status=400)
    question = str(payload.get('question', '')).strip()
    result = answer_tardis_question(question, extra_chunks=build_dynamic_chunks(_get_tardis_rag_entries()))
    status = 200 if result.get('ok', True) else 400
    return JsonResponse(result, status=status)


@require_POST
def tardis_superadmin_login(request):
    username = request.POST.get('username', '').strip()
    password = request.POST.get('password', '').strip()
    if username == TARDIS_SUPERADMIN_USERNAME and password == TARDIS_SUPERADMIN_PASSWORD:
        request.session[TARDIS_SUPERADMIN_SESSION_KEY] = True
        request.session.modified = True
        return JsonResponse({'ok': True})
    return JsonResponse({'ok': False, 'error': 'invalid_credentials', 'message': '账号或密码错误。'}, status=403)


@require_POST
def tardis_superadmin_logout(request):
    request.session.pop(TARDIS_SUPERADMIN_SESSION_KEY, None)
    request.session.modified = True
    return JsonResponse({'ok': True})


@require_POST
def tardis_superadmin_save_entry(request):
    if not _is_tardis_superadmin(request):
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'invalid_json'}, status=400)

    source_text = str(payload.get('source_text', '')).strip()
    if source_text:
        try:
            start_sort = int(payload.get('sort_order') or 100)
        except (TypeError, ValueError):
            start_sort = 100
        extra_keywords = str(payload.get('keywords', '')).strip()
        parsed_entries = extract_tardis_entries_from_text(
            source_text,
            start_sort=start_sort,
            extra_keywords=extra_keywords,
        )
        if not parsed_entries:
            return JsonResponse(
                {'ok': False, 'error': 'empty_extraction', 'message': '这段文字里暂时没提取出可用语料，请换更完整的历史答复内容。'},
                status=400,
            )
        created_entries = []
        for item in parsed_entries:
            entry = TardisRagEntry.objects.create(**item)
            created_entries.append(
                {
                    'id': entry.id,
                    'title': entry.title,
                    'question_hint': entry.question_hint,
                    'answer': entry.answer,
                    'keywords': entry.keywords,
                    'sort_order': entry.sort_order,
                    'is_active': entry.is_active,
                    'updated_at': entry.updated_at.strftime('%Y-%m-%d %H:%M:%S'),
                }
            )
        return JsonResponse({'ok': True, 'created_count': len(created_entries), 'entries': created_entries})

    title = str(payload.get('title', '')).strip()
    answer = str(payload.get('answer', '')).strip()
    if not title or not answer:
        return JsonResponse({'ok': False, 'error': 'missing_fields', 'message': '请填写历史原文，或手动填写标题和回答内容。'}, status=400)

    entry_id = payload.get('id')
    entry = TardisRagEntry.objects.filter(id=entry_id).first() if entry_id else TardisRagEntry()
    if entry_id and entry is None:
        return JsonResponse({'ok': False, 'error': 'not_found'}, status=404)

    entry.title = title
    merged_title = str(payload.get('title', '')).strip()
    entry.question_hint = merged_title
    entry.answer = answer
    entry.keywords = str(payload.get('keywords', '')).strip()
    try:
        entry.sort_order = int(payload.get('sort_order') or 100)
    except (TypeError, ValueError):
        entry.sort_order = 100
    entry.is_active = bool(payload.get('is_active', True))
    entry.save()
    return JsonResponse(
        {
            'ok': True,
            'entry': {
                'id': entry.id,
                'title': entry.title,
                'question_hint': entry.question_hint,
                'answer': entry.answer,
                'keywords': entry.keywords,
                'sort_order': entry.sort_order,
                'is_active': entry.is_active,
                'updated_at': entry.updated_at.strftime('%Y-%m-%d %H:%M:%S'),
            }
        }
    )


@require_POST
def tardis_superadmin_delete_entry(request, entry_id: int):
    if not _is_tardis_superadmin(request):
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)
    entry = TardisRagEntry.objects.filter(id=entry_id).first()
    if entry is None:
        return JsonResponse({'ok': False, 'error': 'not_found'}, status=404)
    entry.delete()
    return JsonResponse({'ok': True})


@ensure_csrf_cookie
def quant_article_tushare(request):
    """量化资源专栏文章：Tushare Pro 数据权限说明"""
    service = _get_api_relay_service('tushare')
    catalog_data, catalog_error = _get_tushare_catalog_payload()
    catalog_examples = catalog_data.get('examples') if isinstance(catalog_data, dict) else {}
    category_names = list(catalog_examples.keys()) if isinstance(catalog_examples, dict) else []
    context = {
        'tushare_service': service,
        'tushare_example_url': '/tushare/daily/000002.SZ/latest',
        'tushare_example_curl': 'curl -H "X-API-Key: <your-api-key>" https://ai-tool.indevs.in/tushare/daily/000002.SZ/latest',
        'tushare_express_news_example_python': _build_tushare_python_example(
            'express_news',
            '/pro/express_news?scope=all&limit=50',
            {'scope': 'all', 'limit': '50'},
            'title,content,datetime,src',
        ),
        'tushare_major_news_example_python': _build_tushare_python_example(
            'major_news',
            '/pro/major_news?src=sina&start_date=2026-03-20%2000:00:00',
            {'src': 'sina', 'start_date': '2026-03-20 00:00:00'},
            'title,content,pub_time,src',
        ),
        'tushare_minute_example_url': '/tushare/minute/000001.SZ/latest?freq=5MIN',
        'tushare_minute_example_curl': 'curl -G -H "X-API-Key: <your-api-key>" --data-urlencode "freq=5MIN" https://ai-tool.indevs.in/tushare/minute/000001.SZ/latest',
        'tushare_minute_60_example_url': '/tushare/minute/000001.SZ/latest?freq=60MIN',
        'tushare_minute_60_example_curl': 'curl -G -H "X-API-Key: <your-api-key>" --data-urlencode "freq=60MIN" https://ai-tool.indevs.in/tushare/minute/000001.SZ/latest',
        'tushare_minute_60_example_python': _build_tushare_latest_python_example('/minute/000001.SZ/latest', {'freq': '60MIN'}),
        'tushare_futures_minute_example_url': '/tushare/minute/RB0/latest?freq=5MIN',
        'tushare_futures_minute_example_curl': 'curl -G -H "X-API-Key: <your-api-key>" --data-urlencode "freq=5MIN" https://ai-tool.indevs.in/tushare/minute/RB0/latest',
        'tushare_futures_minute_example_python': _build_tushare_latest_python_example('/minute/RB0/latest', {'freq': '5MIN'}),
        'tushare_futures_contract_minute_example_url': '/tushare/minute/CU2605.SHFE/latest?freq=5MIN',
        'tushare_futures_contract_minute_example_curl': 'curl -G -H "X-API-Key: <your-api-key>" --data-urlencode "freq=5MIN" https://ai-tool.indevs.in/tushare/minute/CU2605.SHFE/latest',
        'tushare_futures_contract_minute_example_python': _build_tushare_latest_python_example('/minute/CU2605.SHF/latest', {'freq': '5MIN'}),
        'tushare_minute_example_python': _build_tushare_latest_python_example('/minute/000001.SZ/latest', {'freq': '5MIN'}),
        'tushare_catalog': catalog_data,
        'tushare_catalog_error': catalog_error,
        'tushare_catalog_category_names': category_names,
        'tushare_admin_logged_in': _is_tushare_superadmin(request),
        'tushare_rag_entries': TushareRagEntry.objects.order_by('sort_order', '-updated_at', '-id')[:50],
        'tushare_superadmin_username': TUSHARE_SUPERADMIN_USERNAME,
    }
    return render(request, 'tools/quant_article_tushare.html', context)


def quant_article_tushare_rag(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'method_not_allowed'}, status=405)
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'invalid_json', 'message': '请求体需要是合法 JSON。'}, status=400)
    question = str(payload.get('question', '')).strip()
    result = answer_tushare_question(question, extra_chunks=build_tushare_dynamic_chunks(_get_tushare_rag_entries()))
    status = 200 if result.get('ok', True) else 400
    return JsonResponse(result, status=status)


@require_POST
def tushare_superadmin_login(request):
    username = request.POST.get('username', '').strip()
    password = request.POST.get('password', '').strip()
    if username == TUSHARE_SUPERADMIN_USERNAME and password == TUSHARE_SUPERADMIN_PASSWORD:
        request.session[TUSHARE_SUPERADMIN_SESSION_KEY] = True
        request.session.modified = True
        return JsonResponse({'ok': True})
    return JsonResponse({'ok': False, 'error': 'invalid_credentials', 'message': '账号或密码错误。'}, status=403)


@require_POST
def tushare_superadmin_logout(request):
    request.session.pop(TUSHARE_SUPERADMIN_SESSION_KEY, None)
    request.session.modified = True
    return JsonResponse({'ok': True})


@require_POST
def tushare_superadmin_save_entry(request):
    if not _is_tushare_superadmin(request):
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'invalid_json'}, status=400)

    source_text = str(payload.get('source_text', '')).strip()
    if source_text:
        try:
            start_sort = int(payload.get('sort_order') or 100)
        except (TypeError, ValueError):
            start_sort = 100
        extra_keywords = str(payload.get('keywords', '')).strip()
        parsed_entries = extract_tushare_entries_from_text(source_text, start_sort=start_sort, extra_keywords=extra_keywords)
        if not parsed_entries:
            return JsonResponse(
                {'ok': False, 'error': 'empty_extraction', 'message': '这段文字里暂时没提取出可用语料，请换更完整的历史答复内容。'},
                status=400,
            )
        created_entries = []
        for item in parsed_entries:
            entry = TushareRagEntry.objects.create(**item)
            created_entries.append(
                {
                    'id': entry.id,
                    'title': entry.title,
                    'question_hint': entry.question_hint,
                    'answer': entry.answer,
                    'keywords': entry.keywords,
                    'sort_order': entry.sort_order,
                    'is_active': entry.is_active,
                    'updated_at': entry.updated_at.strftime('%Y-%m-%d %H:%M:%S'),
                }
            )
        return JsonResponse({'ok': True, 'created_count': len(created_entries), 'entries': created_entries})

    title = str(payload.get('title', '')).strip()
    answer = str(payload.get('answer', '')).strip()
    if not title or not answer:
        return JsonResponse({'ok': False, 'error': 'missing_fields', 'message': '请填写历史原文，或手动填写标题和回答内容。'}, status=400)

    entry_id = payload.get('id')
    entry = TushareRagEntry.objects.filter(id=entry_id).first() if entry_id else TushareRagEntry()
    if entry_id and entry is None:
        return JsonResponse({'ok': False, 'error': 'not_found'}, status=404)
    entry.title = title
    entry.question_hint = title
    entry.answer = answer
    entry.keywords = str(payload.get('keywords', '')).strip()
    try:
        entry.sort_order = int(payload.get('sort_order') or 100)
    except (TypeError, ValueError):
        entry.sort_order = 100
    entry.is_active = bool(payload.get('is_active', True))
    entry.save()
    return JsonResponse(
        {
            'ok': True,
            'entry': {
                'id': entry.id,
                'title': entry.title,
                'question_hint': entry.question_hint,
                'answer': entry.answer,
                'keywords': entry.keywords,
                'sort_order': entry.sort_order,
                'is_active': entry.is_active,
                'updated_at': entry.updated_at.strftime('%Y-%m-%d %H:%M:%S'),
            }
        }
    )


@require_POST
def tushare_superadmin_delete_entry(request, entry_id: int):
    if not _is_tushare_superadmin(request):
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)
    entry = TushareRagEntry.objects.filter(id=entry_id).first()
    if entry is None:
        return JsonResponse({'ok': False, 'error': 'not_found'}, status=404)
    entry.delete()
    return JsonResponse({'ok': True})


def _get_tushare_catalog_payload():
    catalog_data = {}
    catalog_error = ''
    try:
        upstream = RELAY_HTTP_SESSION.get(
            f'{TUSHARE_RELAY_BASE_URL}/pro/catalog',
            timeout=(3, 8),
        )
        if upstream.ok:
            catalog_data = upstream.json()
        else:
            catalog_error = f'catalog_unavailable:{upstream.status_code}'
    except requests.RequestException as exc:
        catalog_error = f'catalog_unavailable:{exc}'
    catalog_data = _ensure_tushare_catalog_defaults(catalog_data)
    examples = catalog_data.get('examples')
    if isinstance(examples, dict):
        for _, items in examples.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                _enrich_tushare_catalog_item(item)
                example_url = str(item.get('example_url') or '')
                params = item.get('params') if isinstance(item.get('params'), dict) else {}
                api_name = str(item.get('api_name') or '')
                fields = str(item.get('fields') or '').strip()
                item['python_example'] = _build_tushare_python_example(api_name, example_url, params, fields)
    return catalog_data, catalog_error


def _ensure_tushare_catalog_defaults(catalog_data: dict) -> dict:
    data = catalog_data if isinstance(catalog_data, dict) else {}
    categories = data.get('categories')
    if not isinstance(categories, dict):
        categories = {}
        data['categories'] = categories
    examples = data.get('examples')
    if not isinstance(examples, dict):
        examples = {}
        data['examples'] = examples

    default_entries = {
        '新闻数据': [
            {
                'api_name': 'news',
                'params': {'src': 'sina'},
                'fields': 'title,content,datetime,src',
                'example_url': '/pro/news?src=sina',
            },
            {
                'api_name': 'major_news',
                'params': {'src': 'sina', 'start_date': '2026-03-20 00:00:00'},
                'fields': 'title,content,pub_time,src',
                'example_url': '/pro/major_news?src=sina&start_date=2026-03-20%2000:00:00',
            },
            {
                'api_name': 'express_news',
                'params': {'scope': 'all', 'limit': '50'},
                'fields': 'title,content,datetime,src',
                'example_url': '/pro/express_news?scope=all&limit=50',
            },
        ],
        '公告数据（也算新闻数据）': [
            {
                'api_name': 'anns_d',
                'params': {'ann_date': '20260327', 'ts_code': '000001.SZ'},
                'fields': 'ts_code,name,title,ann_date,url',
                'example_url': '/pro/anns_d?ann_date=20260327&ts_code=000001.SZ',
                'retention_policy': {
                    'label': '通常按公告日维度保留 3 到 7 天，历史查询可更久',
                    'recommended_refresh': '盘后到晚间公告集中披露阶段优先刷新',
                    'reason': '公告数据常在交易日盘后和晚间集中更新，按公告日查询最稳定。',
                },
            },
        ],
        '分析师数据': [
            {
                'api_name': 'analyst_rank',
                'params': {'year': '2024', 'limit': '50'},
                'fields': '分析师名称,分析师单位,年度指数,12个月收益率,分析师ID,行业,更新日期,年度',
                'example_url': '/pro/analyst_rank?year=2024&limit=50',
            },
            {
                'api_name': 'analyst_detail',
                'params': {'analyst_id': '11000213851', 'indicator': '最新跟踪成分股', 'limit': '50'},
                'fields': '股票代码,股票名称,调入日期,最新评级日期,当前评级名称,最新价格,阶段涨跌幅',
                'example_url': '/pro/analyst_detail?analyst_id=11000213851&indicator=%E6%9C%80%E6%96%B0%E8%B7%9F%E8%B8%AA%E6%88%90%E5%88%86%E8%82%A1&limit=50',
            },
            {
                'api_name': 'analyst_history',
                'params': {'analyst_id': '11000213851', 'indicator': '历史跟踪成分股', 'limit': '100'},
                'fields': '股票代码,股票名称,调入日期,调出日期,调入时评级名称,调出原因,累计涨跌幅',
                'example_url': '/pro/analyst_history?analyst_id=11000213851&indicator=%E5%8E%86%E5%8F%B2%E8%B7%9F%E8%B8%AA%E6%88%90%E5%88%86%E8%82%A1&limit=100',
            },
            {
                'api_name': 'analyst_history',
                'params': {'analyst_id': '11000213851', 'indicator': '历史指数', 'limit': '240'},
                'fields': 'date,value',
                'example_url': '/pro/analyst_history?analyst_id=11000213851&indicator=%E5%8E%86%E5%8F%B2%E6%8C%87%E6%95%B0&limit=240',
            },
            {
                'api_name': 'research_report',
                'params': {'symbol': '000001', 'limit': '20'},
                'fields': '股票代码,股票简称,报告名称,东财评级,机构,日期,报告PDF链接',
                'example_url': '/pro/research_report?symbol=000001&limit=20',
            },
        ],
    }

    for category_name, default_items in default_entries.items():
        category_examples = examples.get(category_name)
        if not isinstance(category_examples, list):
            category_examples = []
            examples[category_name] = category_examples

        category_items = categories.get(category_name)
        if not isinstance(category_items, list):
            category_items = []
            categories[category_name] = category_items
        for default_item in default_items:
            default_item_url = str(default_item.get('example_url') or '')
            if not any(
                isinstance(item, dict)
                and str(item.get('api_name') or '') == default_item['api_name']
                and str(item.get('example_url') or '') == default_item_url
                for item in category_examples
            ):
                category_examples.append(dict(default_item))
            if default_item['api_name'] not in category_items:
                category_items.append(default_item['api_name'])

    return data


def _enrich_tushare_catalog_item(item: dict) -> dict:
    api_name = str(item.get('api_name') or '').strip()
    params = item.get('params') if isinstance(item.get('params'), dict) else {}
    is_historical_express_news = api_name == 'express_news' and (
        params.get('start_date') or params.get('end_date')
    )
    if api_name == 'express_news':
        if is_historical_express_news:
            item.setdefault('variant_label', '历史区间')
            item.setdefault(
                'intro',
                '按起止日期回补 express_news 历史快讯，适合补抓某段时间窗口内的事件流。',
            )
            item.setdefault(
                'retention_policy',
                {
                    'label': '历史查询直接透传上游，不过本地缓存，不占用磁盘',
                    'recommended_refresh': '需要历史快讯时按需调用，无需主动刷新',
                    'reason': '历史数据由上游 Tushare 原生接口提供，站内心跳 akshare 实时流是另一条路径。',
                },
            )
        else:
            item.setdefault('variant_label', '实时快讯')
            item.setdefault(
                'intro',
                '站内补充快讯新闻口径，适合作为普通新闻与重大新闻之外的第三个高频新闻接口。',
            )
            item.setdefault(
                'retention_policy',
                {
                    'label': '当前缓存 15 分钟，陈旧缓存 2 天内清理',
                    'recommended_refresh': '高频盯盘时每 10 到 15 分钟刷新一次；重大行情时可主动回源',
                    'reason': '快讯流更新速度快，短缓存能抑制重复请求，但不适合像 major_news 一样跨天长保留。',
                },
            )
    if api_name in {'irm_qa_sh', 'irm_qa_sz'}:
        item.setdefault(
            'intro',
            '互动易问答本质上也属于事件驱动的信息披露流，可以视作新闻数据的延伸，不只是静态问答库。',
        )
        item.setdefault(
            'retention_policy',
            {
                'label': '当前缓存 12 小时，陈旧缓存 7 天内清理',
                'recommended_refresh': '盘中到盘后可复用同一批结果；隔夜后或公司有新回复预期时优先刷新',
                'reason': '互动易回复频率通常低于快讯，但仍是事件驱动披露；12 小时足够减少重复回源，同时不会把跨日后的新回复压太久。',
            },
        )
    if api_name == 'analyst_rank':
        item.setdefault(
            'intro',
            '站内补充分析师榜单口径，适合先筛出分析师、机构、行业和分析师 ID，再继续请求详情。',
        )
        item.setdefault(
            'retention_policy',
            {
                'label': '当前缓存 1 天，陈旧缓存 7 天内清理',
                'recommended_refresh': '日内一般无需高频刷新；隔日或跨年度榜单切换时再更新即可',
                'reason': '分析师榜单不是逐分钟变化的数据，日级缓存足够稳妥，也能显著减少重复抓取。',
            },
        )
    if api_name == 'analyst_detail':
        item.setdefault(
            'intro',
            '站内补充分析师成分股与评级详情口径，适合从 analyst_rank 返回的分析师 ID 继续往下查。',
        )
        item.setdefault(
            'retention_policy',
            {
                'label': '当前缓存 12 小时，陈旧缓存 7 天内清理',
                'recommended_refresh': '盘后或次日刷新更有价值；同一交易日内通常复用缓存即可',
                'reason': '分析师跟踪组合和评级变更不会像快讯一样秒级跳动，半天级缓存更匹配真实更新节奏。',
            },
        )
    if api_name == 'analyst_history':
        is_index_series = str(params.get('indicator') or '').strip() == '历史指数'
        item.setdefault('variant_label', '历史指数' if is_index_series else '历史跟踪成分股')
        item.setdefault(
            'intro',
            '站内补充分析师历史口径，可回放分析师历史成分股变动，或直接拉取历史指数时间序列。',
        )
        item.setdefault(
            'retention_policy',
            {
                'label': '当前缓存 1 天，陈旧缓存 14 天内清理',
                'recommended_refresh': '历史回放通常按需调用；同一 analyst_id 在日内可直接复用缓存',
                'reason': '历史分析师口径天然偏静态，日级缓存足够抑制重复抓取，同时保留较长历史查询窗口。',
            },
        )
    if api_name == 'research_report':
        item.setdefault(
            'intro',
            '站内补充个股研报口径，适合按股票代码拉取近期研报标题、评级、机构与 PDF 链接。',
        )
        item.setdefault(
            'retention_policy',
            {
                'label': '当前缓存 12 小时，陈旧缓存 7 天内清理',
                'recommended_refresh': '盘前、盘后或重大事件后刷新最有意义',
                'reason': '研报披露频率通常是日级到小时级，12 小时缓存既能控频，也不会把新研报压太久。',
            },
        )
    return item


def _build_tushare_python_example(api_name: str, example_url: str, params: dict, fields: str = '') -> str:
    parsed = urlsplit(example_url or '')
    path = parsed.path or example_url or ''
    url = f'https://ai-tool.indevs.in/tushare{path}'
    payload = dict(params or {})
    if fields:
        payload['fields'] = fields
    payload_json = json.dumps(payload, ensure_ascii=False, indent=4)
    return (
        "import requests\n\n"
        "API_KEY = \"<your-api-key>\"\n"
        f"URL = \"{url}\"\n"
        f"PARAMS = {payload_json}\n\n"
        "session = requests.Session()\n"
        "session.trust_env = False  # 不继承本机代理环境，避免 Clash/VPN 端口未开时报错\n\n"
        "response = session.get(\n"
        "    URL,\n"
        "    headers={\"X-API-Key\": API_KEY},\n"
        "    params=PARAMS,\n"
        "    timeout=30,\n"
        ")\n"
        "response.raise_for_status()\n"
        "data = response.json()\n"
        f"print(\"api_name=\", \"{api_name}\")\n"
        "print(\"count=\", data.get(\"count\"))\n"
        "print(data)\n"
    )


def _build_tushare_latest_python_example(path: str, params: dict | None = None) -> str:
    url = f'https://ai-tool.indevs.in/tushare{path}'
    payload_json = json.dumps(dict(params or {}), ensure_ascii=False, indent=4)
    return (
        "import requests\n\n"
        "url = \"" + url + "\"\n"
        "headers = {\n"
        "    \"X-API-Key\": \"<your-api-key>\",\n"
        "}\n"
        "params = " + payload_json + "\n\n"
        "response = requests.get(\n"
        "    url,\n"
        "    headers=headers,\n"
        "    params=params,\n"
        "    timeout=30,\n"
        ")\n"
        "response.raise_for_status()\n"
        "print(response.json())\n"
    )


def quant_tushare_catalog(request):
    """Tushare Pro 目录页，适合浏览器搜索和复制示例。"""
    catalog_data, catalog_error = _get_tushare_catalog_payload()
    context = {
        'tushare_catalog': catalog_data,
        'tushare_catalog_error': catalog_error,
        'tushare_minute_example_url': '/tushare/minute/000001.SZ/latest?freq=5MIN',
        'tushare_minute_example_curl': 'curl -G -H "X-API-Key: <your-api-key>" --data-urlencode "freq=5MIN" https://ai-tool.indevs.in/tushare/minute/000001.SZ/latest',
        'tushare_minute_60_example_url': '/tushare/minute/000001.SZ/latest?freq=60MIN',
        'tushare_minute_60_example_curl': 'curl -G -H "X-API-Key: <your-api-key>" --data-urlencode "freq=60MIN" https://ai-tool.indevs.in/tushare/minute/000001.SZ/latest',
        'tushare_futures_minute_example_url': '/tushare/minute/RB0/latest?freq=5MIN',
        'tushare_futures_minute_example_curl': 'curl -G -H "X-API-Key: <your-api-key>" --data-urlencode "freq=5MIN" https://ai-tool.indevs.in/tushare/minute/RB0/latest',
        'tushare_futures_contract_minute_example_url': '/tushare/minute/CU2605.SHFE/latest?freq=5MIN',
        'tushare_futures_contract_minute_example_curl': 'curl -G -H "X-API-Key: <your-api-key>" --data-urlencode "freq=5MIN" https://ai-tool.indevs.in/tushare/minute/CU2605.SHFE/latest',
    }
    return render(request, 'tools/quant_tushare_catalog.html', context)


def edge_inference_hub(request):
    offers = EdgeInferenceOffer.objects.filter(is_active=True).order_by('sort_order', 'price', 'name')
    submitted_request = None
    request_form = EdgeInferenceRequestForm(
        initial={
            'contact_name': request.user.username if request.user.is_authenticated else '',
            'email': request.user.email if request.user.is_authenticated else '',
            'expected_concurrency': 1,
            'expected_hours': 24,
        }
    )

    if request.method == 'POST':
        request_form = EdgeInferenceRequestForm(request.POST)
        selected_offer = EdgeInferenceOffer.objects.filter(pk=request.POST.get('offer_id'), is_active=True).first()
        if request_form.is_valid():
            submitted_request = request_form.save(commit=False)
            submitted_request.user = request.user if request.user.is_authenticated else None
            submitted_request.offer = selected_offer
            if request.user.is_authenticated:
                submitted_request.contact_name = request.user.username or submitted_request.contact_name
                submitted_request.email = request.user.email or submitted_request.email
            submitted_request.save()
            request_form = EdgeInferenceRequestForm(
                initial={
                    'contact_name': request.user.username if request.user.is_authenticated else '',
                    'email': request.user.email if request.user.is_authenticated else '',
                    'expected_concurrency': 1,
                    'expected_hours': 24,
                }
            )

    context = {
        'offers': offers,
        'request_form': request_form,
        'submitted_request': submitted_request,
        'recent_requests': EdgeInferenceRequest.objects.select_related('offer').order_by('-created_at')[:8],
        'my_requests': (
            EdgeInferenceRequest.objects.select_related('offer').filter(user=request.user).order_by('-created_at')[:10]
            if request.user.is_authenticated else []
        ),
    }
    return render(request, 'tools/edge_inference_hub.html', context)


def social_radar_hub(request):
    cleanup_expired_social_radar_results()
    login_form, register_form = _build_auth_forms()
    auth_error = ''
    created_task = None
    if request.method == 'POST' and not request.user.is_authenticated:
        action = request.POST.get('action', '').strip()
        if action == 'login':
            login_form = TTSCreditLoginForm(request.POST, prefix='login')
            if login_form.is_valid():
                user = authenticate(
                    request,
                    username=login_form.cleaned_data['username'],
                    password=login_form.cleaned_data['password'],
                )
                if user is None:
                    auth_error = '用户名或密码错误。'
                else:
                    login(request, user)
                    return redirect('social_radar_hub')
        elif action == 'register':
            register_form = TTSCreditRegisterForm(request.POST, prefix='register')
            if register_form.is_valid():
                user = User.objects.create_user(
                    username=register_form.cleaned_data['username'],
                    email=register_form.cleaned_data['email'],
                    password=register_form.cleaned_data['password'],
                )
                login(request, user)
                return redirect('social_radar_hub')
    context = {
        'login_form': login_form,
        'register_form': register_form,
        'auth_error': auth_error,
        'created_task': created_task,
        'task_type_labels': SOCIAL_RADAR_TYPE_LABELS,
        'my_social_radar_tasks': (
            [_build_social_radar_task_payload(task) for task in SocialRadarTask.objects.filter(user=request.user).order_by('-created_at')[:20]]
            if request.user.is_authenticated else []
        ),
    }
    return render(request, 'tools/social_radar_hub.html', context)


@ensure_csrf_cookie
def codex_briefing_studio(request):
    form = CodexBriefingForm()
    session_key = _ensure_session_key(request)
    tasks = [
        _build_codex_briefing_task_payload(task)
        for task in CodexBriefingTask.objects.filter(session_key=session_key).order_by('-created_at')[:20]
    ]
    return render(
        request,
        'tools/codex_briefing_studio.html',
        {
            'form': form,
            'api_key_hint': '首次输入正确 API Key 后，当前浏览器会话里后续提交无需重复输入',
            'api_key_authed': _is_codex_briefing_authed(request),
            'tasks': tasks,
        },
    )


@require_POST
def codex_briefing_submit_task(request):
    _ensure_session_key(request)
    form = CodexBriefingForm(request.POST)
    if not form.is_valid():
        errors = {key: [str(item) for item in value] for key, value in form.errors.items()}
        return JsonResponse({'ok': False, 'error': 'invalid_form', 'errors': errors}, status=400)
    is_authed = _is_codex_briefing_authed(request)
    submitted_key = form.cleaned_data['api_key'].strip()
    if not is_authed and submitted_key != CODEX_BRIEFING_API_KEY:
        return JsonResponse({'ok': False, 'error': 'invalid_api_key', 'message': 'API Key 不正确。'}, status=403)
    if not is_authed:
        request.session[CODEX_BRIEFING_SESSION_AUTH_KEY] = True
        request.session.modified = True
    source_text = form.cleaned_data['source_text'].strip()
    task = CodexBriefingTask.objects.create(
        session_key=request.session.session_key,
        source_text=source_text,
        source_char_count=len(source_text),
        status=CodexBriefingTask.Status.QUEUED,
        stage='等待执行',
        progress=0,
        message='任务已提交，等待 worker 接单。',
    )
    trigger_codex_briefing_worker()
    return JsonResponse({'ok': True, 'task': _build_codex_briefing_task_payload(task), 'api_key_authed': True})


def codex_briefing_tasks_json(request):
    session_key = _ensure_session_key(request)
    tasks = [
        _build_codex_briefing_task_payload(task)
        for task in CodexBriefingTask.objects.filter(session_key=session_key).order_by('-created_at')[:20]
    ]
    return JsonResponse({'ok': True, 'tasks': tasks})


def codex_briefing_task_status(request, task_id: int):
    session_key = _ensure_session_key(request)
    task = CodexBriefingTask.objects.filter(id=task_id, session_key=session_key).first()
    if task is None:
        return JsonResponse({'ok': False, 'error': 'not_found'}, status=404)
    return JsonResponse({'ok': True, 'task': _build_codex_briefing_task_payload(task)})


@login_required(login_url='social_radar_hub')
def social_radar_logout(request):
    logout(request)
    return redirect('social_radar_hub')


@require_POST
@login_required(login_url='social_radar_hub')
def social_radar_submit_task(request):
    cleanup_expired_social_radar_results()
    task_type = (request.POST.get('task_type') or '').strip()
    allowed = {choice for choice, _ in SocialRadarTask.TaskType.choices}
    if task_type not in allowed:
        return JsonResponse({'ok': False, 'error': 'invalid_task_type'}, status=400)
    params = {'headless': (request.POST.get('headless') or '').strip() in {'1', 'true', 'on'}, 'hydrate_fulltext': (request.POST.get('hydrate_fulltext') or '').strip() not in {'0', 'false', 'off'}}
    for key in ('keyword', 'lang', 'state', 'user_url', 'question_url', 'cookie', 'user_agent', 'zhihu_cookie', 'zhihu_user_agent', 'cdp_url', 'x_cookie'):
        value = (request.POST.get(key) or '').strip()
        if value:
            params[key] = value
    if task_type == SocialRadarTask.TaskType.FOLO:
        params['view'] = int((request.POST.get('view') or '0').strip() or 0)
        params['limit'] = int((request.POST.get('limit') or '20').strip() or 20)
    if (request.POST.get('auto_launch') or '').strip() in {'1', 'true', 'on'}:
        params['auto_launch'] = True
    required_by_type = {
        SocialRadarTask.TaskType.KEYWORD: ['keyword'],
        SocialRadarTask.TaskType.X_ZHIHU_SEARCH: ['keyword', 'zhihu_cookie'],
        SocialRadarTask.TaskType.FOLLOWING: ['state'],
        SocialRadarTask.TaskType.USER_TIMELINE: ['user_url', 'state'],
        SocialRadarTask.TaskType.USER_FOLLOWING: ['user_url', 'state'],
        SocialRadarTask.TaskType.ZHIHU_QUESTION: ['question_url', 'cookie'],
        SocialRadarTask.TaskType.ZHIHU_SEARCH: ['keyword', 'cookie'],
        SocialRadarTask.TaskType.ZHIHU_USER: ['user_url', 'cookie'],
        SocialRadarTask.TaskType.XIAOHONGSHU_USER: ['user_url'],
        SocialRadarTask.TaskType.XIAOHONGSHU_SEARCH: ['keyword', 'cookie'],
        SocialRadarTask.TaskType.FOLO: ['cookie'],
    }
    missing = [field for field in required_by_type.get(task_type, []) if not params.get(field)]
    x_types = {
        SocialRadarTask.TaskType.KEYWORD,
        SocialRadarTask.TaskType.X_ZHIHU_SEARCH,
        SocialRadarTask.TaskType.FOLLOWING,
        SocialRadarTask.TaskType.USER_TIMELINE,
        SocialRadarTask.TaskType.USER_FOLLOWING,
    }
    if task_type in x_types and not params.get('state') and not params.get('x_cookie'):
        missing.append('state_or_x_cookie')
    if missing:
        return JsonResponse({'ok': False, 'error': 'missing_fields', 'message': f'缺少字段: {", ".join(missing)}'}, status=400)
    task = _create_social_radar_task(request.user, task_type, params)
    return JsonResponse({'ok': True, 'task': _build_social_radar_task_payload(task)})


@login_required(login_url='social_radar_hub')
def social_radar_tasks_json(request):
    cleanup_expired_social_radar_results()
    tasks = SocialRadarTask.objects.filter(user=request.user).order_by('-created_at')[:20]
    return JsonResponse({'ok': True, 'tasks': [_build_social_radar_task_payload(task) for task in tasks]})


@login_required(login_url='social_radar_hub')
def social_radar_task_status(request, task_id: int):
    cleanup_expired_social_radar_results()
    task = SocialRadarTask.objects.filter(id=task_id, user=request.user).first()
    if task is None:
        return JsonResponse({'ok': False, 'error': 'not_found'}, status=404)
    return JsonResponse({'ok': True, 'task': _build_social_radar_task_payload(task)})


@require_POST
@login_required(login_url='social_radar_hub')
def social_radar_cancel_task(request, task_id: int):
    task = SocialRadarTask.objects.filter(id=task_id, user=request.user).first()
    if task is None:
        return JsonResponse({'ok': False, 'error': 'not_found'}, status=404)
    if task.status not in {SocialRadarTask.Status.QUEUED, SocialRadarTask.Status.RUNNING, SocialRadarTask.Status.CANCELLING}:
        return JsonResponse({'ok': False, 'error': 'not_cancellable'}, status=400)
    task.cancel_requested = True
    if task.status == SocialRadarTask.Status.QUEUED:
        task.status = SocialRadarTask.Status.CANCELLED
        task.stage = '已停止'
        task.progress = 100
        task.finished_at = timezone.now()
    else:
        task.status = SocialRadarTask.Status.CANCELLING
        task.stage = '正在停止任务'
    task.save(update_fields=['cancel_requested', 'status', 'stage', 'progress', 'finished_at', 'updated_at'])
    return JsonResponse({'ok': True})


@login_required(login_url='social_radar_hub')
def social_radar_task_file(request, task_id: int, relpath: str):
    cleanup_expired_social_radar_results()
    task = SocialRadarTask.objects.filter(id=task_id, user=request.user).first()
    if task is None:
        raise Http404('task_not_found')
    if task.result_expired_at or not task.storage_root:
        return HttpResponseForbidden('result_expired')
    root = Path(task.storage_root).resolve()
    target = (root / relpath).resolve()
    if root not in target.parents and target != root:
        raise Http404('invalid_path')
    if not target.exists() or not target.is_file():
        raise Http404('file_not_found')
    return FileResponse(open(target, 'rb'), content_type=None)


def side_hustle_japan_goods(request):
    """副业实操文章：日本谷子代购"""
    return render(request, 'tools/side_hustle_japan_goods.html')


def side_hustle_xiaohongshu_virtual_store_matrix(request):
    """副业实操文章：小红书虚拟店与矩阵"""
    return render(request, 'tools/side_hustle_xiaohongshu_virtual_store_matrix.html')


def nano_banana_pro_guide(request):
    """Nano Banana Pro 指南文章"""
    return render(request, 'tools/nano_banana_pro_guide.html')


def openclaw_miniqmt_trading_guide(request):
    """OpenClaw + MiniQMT 自动交易实战文章"""
    return render(request, 'tools/openclaw_miniqmt_trading_guide.html')


def openclaw_a_share_auto_trading_guide(request):
    """OpenClaw A股自动量化交易系统实战文章"""
    return render(request, 'tools/openclaw_a_share_auto_trading_guide.html')


def openclaw_guardian_agent_guide(request):
    """OpenClaw 互备 Agent 与自动巡检指南"""
    return render(request, 'tools/openclaw_guardian_agent_guide.html')


def openclaw_ai_learning_workflow_guide(request):
    """OpenClaw AI 学习工作流指南"""
    return render(request, 'tools/openclaw_ai_learning_workflow_guide.html')


def openclaw_ai_monetization_survival_guide(request):
    """OpenClaw 专栏文章：AI 变现生存指南"""
    return render(request, 'tools/openclaw_ai_monetization_survival_guide.html')


def opencli_guide(request):
    """OpenClaw 专栏文章：OpenCLI 中文教程"""
    return render(request, 'tools/opencli_guide.html')


def llm_algorithm_engineer_sources_guide(request):
    """AI算法专栏文章：大模型算法工程师信息源指南"""
    return render(request, 'tools/llm_algorithm_engineer_sources_guide.html')


def yaoban_research_learning_guide(request):
    """AI算法专栏文章：姚班学习、科研与职业路径总结"""
    return render(request, 'tools/yaoban_research_learning_guide.html')


def _get_credit_account(user):
    account, created = TTSCreditAccount.objects.get_or_create(
        user=user,
        defaults={'is_unlimited': True},
    )
    if not account.is_unlimited:
        account.is_unlimited = True
        account.save(update_fields=['is_unlimited', 'updated_at'])
    return account


def _build_auth_forms():
    return TTSCreditLoginForm(prefix='login'), TTSCreditRegisterForm(prefix='register')


def _format_elapsed(total_seconds):
    total_seconds = max(int(total_seconds), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f'{hours}小时 {minutes}分 {seconds}秒'
    if minutes:
        return f'{minutes}分 {seconds}秒'
    return f'{seconds}秒'


def _build_order_elapsed(order):
    start_at = order.created_at or timezone.now()
    if order.status == TTSOrder.Status.DELIVERED and order.delivered_at:
        end_at = order.delivered_at
    elif order.status == TTSOrder.Status.CANCELLED:
        end_at = order.updated_at or timezone.now()
    else:
        end_at = timezone.now()
    elapsed_seconds = max(int((end_at - start_at).total_seconds()), 0)
    return {
        'elapsed_seconds': elapsed_seconds,
        'elapsed_text': _format_elapsed(elapsed_seconds),
    }


def _get_tts_order_status_display(order) -> str:
    if order.status in {TTSOrder.Status.QUEUED, TTSOrder.Status.GENERATING}:
        return '生成中'
    return order.get_status_display()


def _build_order_progress(order):
    rules = get_tts_runtime_rules()
    total_chunks = estimate_total_chunks(order.char_count)
    progress = 0
    message = '等待处理'
    matched = None
    detail = {
        'done_chars': 0,
        'total_chars': order.char_count,
        'current_chunk': 0,
        'total_chunks': total_chunks,
        'chunk_chars': 0,
        'updated_at': timezone.localtime(order.updated_at).strftime('%Y-%m-%d %H:%M:%S') if order.updated_at else '',
    }
    if order.processing_log:
        matched = PROGRESS_RE.search(order.processing_log)
        if matched:
            progress = max(0, min(int(matched.group(1)), 100))
            message = matched.group(2).strip()
        else:
            message = order.processing_log.strip().splitlines()[-1]

    if order.status == TTSOrder.Status.QUEUED:
        progress = max(progress, 5)
        message = '生成中'
    elif order.status == TTSOrder.Status.GENERATING:
        progress = max(progress, 15)
        if message == '等待处理' or not matched:
            message = '生成中'
    elif order.status == TTSOrder.Status.DELIVERED:
        progress = 100
        message = '已生成完成'
    elif order.status == TTSOrder.Status.CANCELLED:
        progress = 0
        message = '任务已取消'

    if order.is_output_expired:
        message = '音频已过期并清理'

    chunk_match = CHUNK_PROGRESS_RE.search(message)
    if chunk_match:
        detail.update({key: int(value) for key, value in chunk_match.groupdict().items()})
    else:
        whole_text_match = WHOLE_TEXT_RE.search(message)
        batch_match = CHUNK_BATCH_RE.search(message)
        total_match = CHUNK_TOTAL_RE.search(message)
        if whole_text_match:
            parsed = {key: int(value) for key, value in whole_text_match.groupdict().items()}
            detail.update(
                {
                    'done_chars': parsed['done_chars'],
                    'total_chars': parsed['total_chars'],
                    'current_chunk': 1,
                    'total_chunks': 1,
                    'chunk_chars': parsed['chunk_chars'],
                }
            )
        elif batch_match:
            detail.update(
                {
                    'current_chunk': int(batch_match.group('batch_start')),
                    'total_chunks': int(batch_match.group('total_chunks')),
                }
            )
        elif total_match:
            detail['total_chunks'] = int(total_match.group('total_chunks'))
    if order.status == TTSOrder.Status.DELIVERED:
        detail.update(
            {
                'done_chars': order.char_count,
                'total_chars': order.char_count,
                'current_chunk': total_chunks,
                'total_chunks': total_chunks,
                'chunk_chars': 0,
            }
        )

    if order.char_count <= 500:
        eta_hint = '短文本通常几十秒到 2 分钟，当前按整段直接生成。'
    elif order.char_count <= rules['direct_max_chars']:
        eta_hint = f'当前 {rules["direct_max_chars"]} 字以内会整段直出，通常会比切段拼接更快。'
    else:
        eta_hint = f'长文本超过 {rules["direct_max_chars"]} 字后，会按每 {rules["chunk_chars"]} 字切分，并按约 {rules["batch_chars"]} 字组批顺序生成。'

    return {
        'progress_percent': progress,
        'progress_message': message,
        'eta_hint': eta_hint,
        'detail': detail,
        'rules': rules,
    }


def _can_access_order(request, order, *, email=''):
    if request.user.is_authenticated and order.user_id == request.user.id:
        return True
    return bool(email and email == order.email)


@transaction.atomic
def _refund_tts_order_credits(order):
    if not order.user_id or not order.payment_reference.startswith('CREDIT-'):
        return
    if order.ledger_entries.filter(entry_type=TTSCreditLedger.EntryType.REFUND).exists():
        return
    account = TTSCreditAccount.objects.select_for_update().get(user=order.user)
    if not account.is_unlimited:
        account.char_balance += order.char_count
    account.total_used_chars = max(account.total_used_chars - order.char_count, 0)
    account.save(update_fields=['char_balance', 'total_used_chars', 'updated_at'])
    TTSCreditLedger.objects.create(
        user=order.user,
        entry_type=TTSCreditLedger.EntryType.REFUND,
        char_delta=order.char_count,
        balance_after=account.char_balance,
        tts_order=order,
        note=f'用户取消 TTS 订单 {order.order_no}，退回 {order.char_count} 字',
    )


@transaction.atomic
def _cancel_tts_order(order):
    locked = TTSOrder.objects.select_for_update().get(pk=order.pk)
    if locked.status == TTSOrder.Status.CANCELLED:
        return locked, 'already_cancelled'
    if locked.status == TTSOrder.Status.DELIVERED:
        return locked, 'already_delivered'
    if locked.status == TTSOrder.Status.QUEUED:
        _refund_tts_order_credits(locked)
        locked.status = TTSOrder.Status.CANCELLED
        locked.cancel_requested = False
        locked.processing_log = f'{timezone.now():%F %T} [进度 0%] 用户已取消任务，额度已退回'
        locked.save(update_fields=['status', 'cancel_requested', 'processing_log', 'updated_at'])
        return locked, 'cancelled'
    if locked.status == TTSOrder.Status.GENERATING:
        _refund_tts_order_credits(locked)
        locked.status = TTSOrder.Status.CANCELLED
        locked.cancel_requested = False
        locked.processing_log = f'{timezone.now():%F %T} [进度 0%] 用户强制取消任务，额度已退回'
        locked.save(update_fields=['status', 'cancel_requested', 'processing_log', 'updated_at'])

        interrupted_orders = list(
            TTSOrder.objects.select_for_update()
            .filter(payment_status=TTSOrder.PaymentStatus.PAID, status=TTSOrder.Status.GENERATING)
            .exclude(pk=locked.pk)
        )
        for interrupted in interrupted_orders:
            if interrupted.cancel_requested:
                _refund_tts_order_credits(interrupted)
                interrupted.status = TTSOrder.Status.CANCELLED
                interrupted.cancel_requested = False
                interrupted.processing_log = f'{timezone.now():%F %T} [进度 0%] 用户强制取消任务，额度已退回'
                interrupted.save(update_fields=['status', 'cancel_requested', 'processing_log', 'updated_at'])
            else:
                interrupted.status = TTSOrder.Status.QUEUED
                interrupted.processing_log = f'{timezone.now():%F %T} [进度 5%] worker 已重启，中断任务重新入队'
                interrupted.save(update_fields=['status', 'processing_log', 'updated_at'])

        stopped = stop_tts_worker()
        transaction.on_commit(lambda: trigger_tts_generation(''))
        return locked, 'force_cancelled' if stopped else 'cancelled'
    return locked, 'not_cancellable'


@transaction.atomic
def _apply_recharge_order(recharge_order, provider, amount, payment_reference, payload):
    if recharge_order.payment_status == TTSCreditRechargeOrder.PaymentStatus.PAID:
        return recharge_order

    account = _get_credit_account(recharge_order.user)
    account.char_balance += recharge_order.char_amount
    account.total_purchased_chars += recharge_order.char_amount
    account.save(update_fields=['char_balance', 'total_purchased_chars', 'updated_at'])

    TTSCreditLedger.objects.create(
        user=recharge_order.user,
        entry_type=TTSCreditLedger.EntryType.RECHARGE,
        char_delta=recharge_order.char_amount,
        balance_after=account.char_balance,
        recharge_order=recharge_order,
        note=f'自动充值到账 {amount} 元，渠道={provider}，流水号={payment_reference}',
    )

    now = timezone.now()
    recharge_order.payment_status = TTSCreditRechargeOrder.PaymentStatus.PAID
    recharge_order.payment_provider = provider
    recharge_order.payment_reference = payment_reference
    recharge_order.payment_callback_payload = payload
    recharge_order.paid_at = now
    recharge_order.payment_verified_at = now
    recharge_order.payment_error = ''
    recharge_order.save()
    return recharge_order


def _build_qr_data_uri(content: str) -> str:
    if not content:
        return ''
    img = qrcode.make(content)
    buf = BytesIO()
    img.save(buf, format='PNG')
    return f"data:image/png;base64,{b64encode(buf.getvalue()).decode('utf-8')}"


def _build_recent_tts_orders(user, limit=10):
    base_qs = user.tts_orders.order_by('-created_at')
    active_orders = list(
        user.tts_orders.filter(
            status__in=[TTSOrder.Status.GENERATING, TTSOrder.Status.QUEUED]
        ).order_by('-updated_at', '-created_at')
    )
    recent_orders = list(base_qs[:limit])
    merged_orders = []
    seen_order_ids = set()

    for order in active_orders + recent_orders:
        if order.pk in seen_order_ids:
            continue
        merged_orders.append(order)
        seen_order_ids.add(order.pk)
        if len(merged_orders) >= limit:
            break
    for order in merged_orders:
        order.status_display = _get_tts_order_status_display(order)
    return merged_orders


@transaction.atomic
def _create_credit_tts_order(user, form):
    account = TTSCreditAccount.objects.select_for_update().get(pk=_get_credit_account(user).pk)
    source_text = form.cleaned_data['source_text'].strip()
    char_count = len(source_text)

    if account.is_unlimited:
        balance_after = account.char_balance
    else:
        remaining_quota = max(account.total_purchased_chars - account.total_used_chars, 0)
        available_chars = min(account.char_balance, remaining_quota)
        if char_count > available_chars:
            form.add_error(
                'source_text',
                f'当前可转换额度不足，需要 {char_count} 字，当前最多还能转换 {available_chars} 字。'
            )
            return None
        account.char_balance = available_chars - char_count
        balance_after = account.char_balance

    account.total_used_chars += char_count
    account.save(update_fields=['char_balance', 'total_used_chars', 'updated_at'])

    order = form.save(commit=False)
    order.user = user
    order.contact_name = user.username
    order.email = user.email or f'{user.username}@local.invalid'
    order.wechat = ''
    order.company = ''
    order.business_usage = True
    order.estimated_price = Decimal('0.00')
    order.final_price = Decimal('0.00')
    order.payment_status = TTSOrder.PaymentStatus.PAID
    order.status = TTSOrder.Status.QUEUED
    order.payment_provider = ''
    order.payment_reference = f'CREDIT-{timezone.now():%Y%m%d%H%M%S}'
    order.paid_at = timezone.now()
    order.payment_verified_at = timezone.now()
    order.processing_log = f'{timezone.now():%F %T} 用户使用额度提交，扣除 {char_count} 字'
    order.save()
    transaction.on_commit(lambda: trigger_tts_generation(order.order_no))

    TTSCreditLedger.objects.create(
        user=user,
        entry_type=TTSCreditLedger.EntryType.CONSUME,
        char_delta=-char_count,
        balance_after=balance_after,
        tts_order=order,
        note=f'TTS 提交{"（无限额度用户，不扣余额）" if account.is_unlimited else ""}，记录 {char_count} 字',
    )
    return order


@transaction.atomic
def _create_regenerated_tts_order(user, original_order):
    account = TTSCreditAccount.objects.select_for_update().get(pk=_get_credit_account(user).pk)
    source_text = (original_order.source_text or '').strip()
    char_count = len(source_text)
    if not source_text or char_count <= 0:
        return None, 'empty_source'

    if account.is_unlimited:
        balance_after = account.char_balance
    else:
        remaining_quota = max(account.total_purchased_chars - account.total_used_chars, 0)
        available_chars = min(account.char_balance, remaining_quota)
        if char_count > available_chars:
            return None, 'insufficient_quota'
        account.char_balance = available_chars - char_count
        balance_after = account.char_balance

    account.total_used_chars += char_count
    account.save(update_fields=['char_balance', 'total_used_chars', 'updated_at'])

    now = timezone.now()
    new_order = TTSOrder.objects.create(
        user=user,
        contact_name=user.username,
        email=user.email or original_order.email or f'{user.username}@local.invalid',
        wechat='',
        company='',
        source_text=source_text,
        voice_preset=original_order.voice_preset,
        style_notes=original_order.style_notes,
        business_usage=True,
        delivery_format=original_order.delivery_format,
        estimated_price=Decimal('0.00'),
        final_price=Decimal('0.00'),
        payment_status=TTSOrder.PaymentStatus.PAID,
        status=TTSOrder.Status.QUEUED,
        payment_provider='',
        payment_reference=f'CREDIT-REGEN-{now:%Y%m%d%H%M%S}',
        paid_at=now,
        payment_verified_at=now,
        processing_log=f'{now:%F %T} 基于订单 {original_order.order_no} 重新生成，扣除 {char_count} 字',
    )
    transaction.on_commit(lambda: trigger_tts_generation(new_order.order_no))

    TTSCreditLedger.objects.create(
        user=user,
        entry_type=TTSCreditLedger.EntryType.CONSUME,
        char_delta=-char_count,
        balance_after=balance_after,
        tts_order=new_order,
        note=f'TTS 重新生成，来源订单 {original_order.order_no}，记录 {char_count} 字{"（无限额度用户，不扣余额）" if account.is_unlimited else ""}',
    )
    return new_order, 'ok'


def tts_studio(request):
    """TTS 额度充值与消费页面"""
    login_form, register_form = _build_auth_forms()
    recharge_form = TTSRechargeForm()
    consume_form = TTSCreditConsumeForm(
        initial={
            'voice_preset': TTSOrder.VoicePreset.SERENA,
            'delivery_format': TTSOrder.DeliveryFormat.MP3,
        }
    )
    auth_error = ''

    if request.method == 'POST':
        action = request.POST.get('action', '').strip()
        if action == 'login':
            login_form = TTSCreditLoginForm(request.POST, prefix='login')
            if login_form.is_valid():
                user = authenticate(
                    request,
                    username=login_form.cleaned_data['username'],
                    password=login_form.cleaned_data['password'],
                )
                if user is None:
                    auth_error = '用户名或密码错误。'
                else:
                    login(request, user)
                    _get_credit_account(user)
                    return redirect('tts_studio')
        elif action == 'register':
            register_form = TTSCreditRegisterForm(request.POST, prefix='register')
            if register_form.is_valid():
                user = User.objects.create_user(
                    username=register_form.cleaned_data['username'],
                    email=register_form.cleaned_data['email'],
                    password=register_form.cleaned_data['password'],
                )
                _get_credit_account(user)
                login(request, user)
                return redirect('tts_studio')
        elif action == 'recharge' and request.user.is_authenticated:
            recharge_form = TTSRechargeForm(request.POST)
            if recharge_form.is_valid():
                char_amount = recharge_form.cleaned_data['char_amount']
                recharge_order = TTSCreditRechargeOrder.objects.create(
                    user=request.user,
                    char_amount=char_amount,
                    amount=build_recharge_amount(char_amount),
                )
                return redirect('tts_recharge_checkout', order_no=recharge_order.order_no)
        elif action == 'submit_tts' and request.user.is_authenticated:
            consume_form = TTSCreditConsumeForm(request.POST)
            if consume_form.is_valid():
                order = _create_credit_tts_order(request.user, consume_form)
                if order is not None:
                    return redirect('tts_order_submitted', order_no=order.order_no)

    voice_cards = [
        {
            'key': key,
            'name': value['display_name'],
            'summary': value.get('summary', ''),
            'instruction': value['instruction'],
        }
        for key, value in VOICE_PRESET_CONFIG.items()
        if value.get('selectable', True)
    ]
    account = _get_credit_account(request.user) if request.user.is_authenticated else None
    api_relay_cards = _build_api_relay_service_cards(request)
    api_accesses = [card['access'] for card in api_relay_cards if card['access']]
    recent_orders = _build_recent_tts_orders(request.user, limit=20) if request.user.is_authenticated else []
    recent_recharges = request.user.tts_recharge_orders.order_by('-created_at')[:10] if request.user.is_authenticated else []
    context = {
        'login_form': login_form,
        'register_form': register_form,
        'recharge_form': recharge_form,
        'consume_form': consume_form,
        'voice_cards': voice_cards,
        'pricing_examples': [
            {**item, 'price': Decimal('0.00')}
            for item in DEFAULT_RECHARGE_PACKS
        ],
        'sales_wechat': os.getenv('TTS_SALES_WECHAT', 'dreamsjtuai'),
        'payment_note': os.getenv('TTS_PAYMENT_NOTE', '当前 TTS 面向全体用户免费开放。注册登录后可直接提交文本进入生成队列，不需要充值或付款。'),
        'manual_payment_notice': MANUAL_PAYMENT_NOTICE,
        'auth_error': auth_error,
        'account': account,
        'api_accesses': api_accesses,
        'api_relay_cards': api_relay_cards,
        'recent_orders': recent_orders,
        'recent_recharges': recent_recharges,
    }
    return render(request, 'tools/tts_studio.html', context)


def api_relay_hub(request):
    context = {
        'api_relay_cards': _build_api_relay_service_cards(request),
    }
    return render(request, 'tools/api_relay_hub.html', context)


@login_required(login_url='tts_studio')
def tts_logout(request):
    logout(request)
    return redirect('tts_studio')


@login_required(login_url='tts_studio')
def tts_recharge_checkout(request, order_no):
    recharge_order = get_object_or_404(TTSCreditRechargeOrder, order_no=order_no, user=request.user)
    proof_form = TTSCreditRechargeProofForm(instance=recharge_order)
    if request.method == 'POST' and recharge_order.payment_status == TTSCreditRechargeOrder.PaymentStatus.UNPAID:
        proof_form = TTSCreditRechargeProofForm(request.POST, request.FILES, instance=recharge_order)
        if proof_form.is_valid():
            updated = proof_form.save(commit=False)
            updated.payment_proof_uploaded_at = timezone.now()
            updated.save()
            return redirect('tts_recharge_checkout', order_no=recharge_order.order_no)
    context = {
        'recharge_order': recharge_order,
        'sales_wechat': os.getenv('TTS_SALES_WECHAT', 'dreamsjtuai'),
        'proof_form': proof_form,
        'wechat_qr_data_uri': _build_qr_data_uri('weixin://'),
        'manual_payment_notice': MANUAL_PAYMENT_NOTICE,
    }
    return render(request, 'tools/tts_recharge_checkout.html', context)


@login_required(login_url='tts_studio')
def tts_recharge_status(request, order_no):
    recharge_order = get_object_or_404(TTSCreditRechargeOrder, order_no=order_no, user=request.user)
    account = _get_credit_account(request.user)
    return JsonResponse(
        {
            'order_no': recharge_order.order_no,
            'payment_status': recharge_order.payment_status,
            'payment_status_display': recharge_order.get_payment_status_display(),
            'char_amount': recharge_order.char_amount,
            'char_balance': account.char_balance,
            'paid_at': recharge_order.paid_at.isoformat() if recharge_order.paid_at else '',
            'proof_uploaded': bool(recharge_order.payment_proof),
        }
    )


def tts_order_submitted(request, order_no):
    """TTS 订单提交成功页"""
    order = get_object_or_404(TTSOrder, order_no=order_no)
    _expire_order_output_if_needed(order)
    order.refresh_from_db()
    tier_name = build_quote(order.char_count, order.business_usage)[1]
    context = {
        'order': order,
        'order_status_display': _get_tts_order_status_display(order),
        'order_progress': _build_order_progress(order),
        'order_elapsed': _build_order_elapsed(order),
        'tier_name': tier_name,
        'turnaround': build_turnaround(order.char_count),
        'sales_wechat': os.getenv('TTS_SALES_WECHAT', 'dreamsjtuai'),
        'payment_note': os.getenv('TTS_PAYMENT_NOTE', '额度已扣减，订单已直接进入生成队列。'),
        'proof_form': TTSPaymentProofForm(instance=order),
        'manual_payment_notice': MANUAL_PAYMENT_NOTICE,
        'can_cancel': order.status in {TTSOrder.Status.QUEUED, TTSOrder.Status.GENERATING},
        'can_regenerate': bool(order.user_id and order.status in {TTSOrder.Status.DELIVERED, TTSOrder.Status.CANCELLED}),
    }
    return render(request, 'tools/tts_order_submitted.html', context)


def tts_order_query(request):
    """公开订单查询页"""
    form = TTSOrderLookupForm(request.GET or None)
    order = None
    proof_form = None
    if request.method == 'GET' and form.is_valid():
        order = TTSOrder.objects.filter(
            order_no=form.cleaned_data['order_no'].strip(),
            email=form.cleaned_data['email'].strip(),
        ).first()
        if order is None:
            form.add_error(None, '没有找到匹配的订单，请检查订单号和邮箱。')
        else:
            _expire_order_output_if_needed(order)
            order.refresh_from_db()
            proof_form = TTSPaymentProofForm(instance=order)

    context = {
        'form': form,
        'order': order,
        'order_status_display': _get_tts_order_status_display(order) if order else '',
        'order_progress': _build_order_progress(order) if order else None,
        'order_elapsed': _build_order_elapsed(order) if order else None,
        'proof_form': proof_form,
        'sales_wechat': os.getenv('TTS_SALES_WECHAT', 'dreamsjtuai'),
        'manual_payment_notice': MANUAL_PAYMENT_NOTICE,
        'can_cancel': bool(order and order.status in {TTSOrder.Status.QUEUED, TTSOrder.Status.GENERATING}),
        'can_regenerate': bool(order and order.user_id and order.status in {TTSOrder.Status.DELIVERED, TTSOrder.Status.CANCELLED}),
    }
    return render(request, 'tools/tts_order_query.html', context)


def _expire_order_output_if_needed(order):
    if not order.output_file or not order.is_output_expired:
        return False
    if getattr(order.output_file, 'path', ''):
        try:
            archive_tts_file(order, Path(order.output_file.path))
        except Exception:
            pass
    file_name = order.output_file.name
    order.output_file.delete(save=False)
    timestamp = timezone.now().strftime('%F %T')
    log_parts = [part for part in [order.processing_log.strip(), f'{timestamp} 交付文件已过期并清理: {file_name}'] if part]
    order.output_file = ''
    order.output_duration_seconds = None
    order.processing_log = '\n'.join(log_parts)
    order.save(update_fields=['output_file', 'output_duration_seconds', 'processing_log', 'updated_at'])
    return True


def tts_order_status(request, order_no):
    order = get_object_or_404(TTSOrder, order_no=order_no)
    email = request.GET.get('email', '').strip()
    if not _can_access_order(request, order, email=email):
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)

    _expire_order_output_if_needed(order)
    order.refresh_from_db()

    progress = _build_order_progress(order)
    return JsonResponse(
        {
            'ok': True,
            'order_no': order.order_no,
            'status': order.status,
            'status_display': _get_tts_order_status_display(order),
            'payment_status': order.payment_status,
            'payment_status_display': order.get_payment_status_display(),
            'progress_percent': progress['progress_percent'],
            'progress_message': progress['progress_message'],
            'eta_hint': progress['eta_hint'],
            'progress_detail': progress['detail'],
            'elapsed_seconds': _build_order_elapsed(order)['elapsed_seconds'],
            'elapsed_text': _build_order_elapsed(order)['elapsed_text'],
            'processing_log': order.processing_log,
            'cancel_requested': order.cancel_requested,
            'output_file_url': '' if not order.output_file or order.is_output_expired else f'/tts-studio/download/{order.order_no}/?email={email}',
            'output_expires_at': order.output_expires_at.isoformat() if order.output_expires_at else '',
            'is_output_expired': order.is_output_expired,
        }
    )


def tts_download_order_output(request, order_no):
    order = get_object_or_404(TTSOrder, order_no=order_no)
    email = request.GET.get('email', '').strip()
    if not _can_access_order(request, order, email=email):
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)
    _expire_order_output_if_needed(order)
    order.refresh_from_db()
    if not order.output_file or order.is_output_expired:
        raise Http404('音频已过期或不存在')
    response = FileResponse(open(order.output_file.path, 'rb'), as_attachment=True, filename=os.path.basename(order.output_file.name))
    return response


def api_relay_proxy(request, service_slug: str, relay_path: str = ''):
    service = _get_api_relay_service(service_slug)
    if service is None:
        raise Http404('Relay service not found')
    if service.slug == 'tushare' and (relay_path or '').strip('/').startswith('minute/'):
        return _tushare_minute_proxy(request, service, relay_path)
    allowed, deny_message = _relay_path_allowed_for_service(service, relay_path)
    if not allowed:
        return JsonResponse(
            {
                'ok': False,
                'error': 'path_not_allowed',
                'message': deny_message,
                'apply_url': service.apply_url or '/api-relay/',
            },
            status=403,
        )
    if request.method.upper() not in service.allowed_method_set:
        return JsonResponse(
            {
                'ok': False,
                'error': 'method_not_allowed',
                'message': f'当前服务不允许 {request.method.upper()} 方法。',
            },
            status=405,
        )
    access, upstream_user, auth_error = _authorize_api_relay_request(request, service)
    if auth_error is not None:
        return auth_error

    upstream_base = service.base_url.rstrip('/')
    if (
        service.slug == 'tushare'
        and _is_tushare_local_proxy(relay_path)
    ):
        upstream_params = dict(request.GET.items())
        upstream_params.update(_parse_json_mapping(service.upstream_query_params))
        if _is_tushare_local_news_proxy(relay_path) and not _is_tushare_express_news_realtime_only(relay_path, upstream_params):
            # 历史日期查询：直接透传上游，不过 DB 缓存，不落盘
            upstream_url = f'{upstream_base}/{relay_path.lstrip("/")}'
            cache_lock = nullcontext()
            cache_enabled = False
        else:
            cache_entry, cache_key, cache_query_string = _get_tushare_news_cache_entry(relay_path, upstream_params)
            if cache_entry is not None:
                return _build_cached_tushare_news_response(cache_entry, upstream_base)
            is_leader, lease_token = _claim_tushare_replay_lease(cache_key, relay_path, cache_query_string)
            if not is_leader:
                cache_entry = _wait_for_tushare_replay_cache_fill(relay_path, upstream_params, cache_key)
                if cache_entry is not None:
                    return _build_cached_tushare_news_response(cache_entry, upstream_base)
                return JsonResponse(
                    {
                        'ok': False,
                        'error': 'relay_busy',
                        'message': f'{service.name} 正在刷新同一份数据，请稍后重试。',
                    },
                    status=503,
                )
            try:
                try:
                    payload = _fetch_tushare_local_proxy_payload(relay_path, upstream_params)
                except ValueError as exc:
                    return JsonResponse(
                        {
                            'ok': False,
                            'error': 'invalid_params',
                            'message': str(exc),
                        },
                        status=400,
                    )
                except Exception as exc:
                    return JsonResponse(
                        {
                            'ok': False,
                            'error': 'relay_unavailable',
                            'message': f'{service.name} 不可用: {exc}',
                        },
                        status=503,
                    )
                return _build_tushare_local_news_response(
                    payload,
                    service,
                    cache_key=cache_key,
                    cache_query_string=cache_query_string,
                    relay_path=relay_path,
                    cached=False,
                )
            finally:
                _release_tushare_replay_lease(cache_key, lease_token)
    upstream_url = f'{upstream_base}/{relay_path.lstrip("/")}' if relay_path else f'{upstream_base}/'
    upstream_params = dict(request.GET.items())
    upstream_params.update(_parse_json_mapping(service.upstream_query_params))
    cache_enabled = _should_use_tushare_news_cache(service, request, relay_path)
    cache_entry = None
    cache_key = ''
    cache_query_string = ''
    if cache_enabled:
        cache_entry, cache_key, cache_query_string = _get_tushare_news_cache_entry(relay_path, upstream_params)
        if cache_entry is not None:
            return _build_cached_tushare_news_response(cache_entry, upstream_base)
        cache_lock = nullcontext()
    else:
        cache_lock = nullcontext()
    upstream_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {'host', 'content-length', 'connection', 'cookie', 'authorization'}
    }
    upstream_headers.update(_parse_json_mapping(service.upstream_headers))
    if upstream_user is not None:
        upstream_headers['X-Ai-Tools-User-Id'] = str(upstream_user.id)
        upstream_headers['X-Ai-Tools-Username'] = upstream_user.username
        upstream_headers['X-Ai-Tools-User-Email'] = upstream_user.email or ''
    upstream_headers['X-Ai-Tools-Relay-Service'] = service.slug
    with cache_lock:
        if cache_enabled:
            is_leader, lease_token = _claim_tushare_replay_lease(cache_key, relay_path, cache_query_string)
            if not is_leader:
                cache_entry = _wait_for_tushare_replay_cache_fill(relay_path, upstream_params, cache_key)
                if cache_entry is not None:
                    return _build_cached_tushare_news_response(cache_entry, upstream_base)
                return JsonResponse(
                    {
                        'ok': False,
                        'error': 'relay_busy',
                        'message': f'{service.name} 正在刷新同一份数据，请稍后重试。',
                    },
                    status=503,
                )
        else:
            lease_token = ''
        try:
            try:
                upstream = RELAY_HTTP_SESSION.request(
                    method=request.method,
                    url=upstream_url,
                    params=upstream_params,
                    data=request.body if request.method not in {'GET', 'HEAD'} else None,
                    headers=upstream_headers,
                    timeout=(5, max(int(service.timeout_seconds or 60), 5)),
                )
            except requests.RequestException as exc:
                return JsonResponse(
                    {
                        'ok': False,
                        'error': 'relay_unavailable',
                        'message': f'{service.name} 不可用: {exc}',
                    },
                    status=503,
                )

            hop_by_hop_headers = {
                'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
                'te', 'trailers', 'transfer-encoding', 'upgrade', 'content-encoding',
            }
            response = HttpResponse(
                upstream.content,
                status=upstream.status_code,
                content_type=upstream.headers.get('Content-Type', 'application/octet-stream'),
            )
            for key, value in upstream.headers.items():
                if key.lower() in hop_by_hop_headers or key.lower() == 'content-length':
                    continue
                response[key] = value
            response['X-Api-Relay-Service'] = service.slug
            response['X-Api-Relay-Upstream'] = upstream_base
            if cache_enabled:
                response['X-Api-Relay-Cache'] = 'MISS'
                if upstream.status_code == 200:
                    response_text = getattr(upstream, 'text', '')
                    if not response_text and getattr(upstream, 'content', None) is not None:
                        try:
                            response_text = upstream.content.decode('utf-8')
                        except Exception:
                            response_text = upstream.content.decode('utf-8', errors='replace')
                    _store_tushare_replay_cache(
                        cache_key=cache_key,
                        relay_path=relay_path,
                        query_string=cache_query_string,
                        response_body=response_text,
                        status_code=upstream.status_code,
                        content_type=upstream.headers.get('Content-Type', 'application/json'),
                        params=upstream_params,
                    )
            return response
        finally:
            if cache_enabled:
                _release_tushare_replay_lease(cache_key, lease_token)


def tushare_proxy(request, relay_path: str = ''):
    normalized = (relay_path or '').strip('/')
    accept = (request.headers.get('Accept') or '').lower()
    wants_html = request.method == 'GET' and 'text/html' in accept and not _extract_api_key_from_request(request)
    if normalized == 'pro/catalog' and wants_html:
        return quant_tushare_catalog(request)
    return api_relay_proxy(request, 'tushare', relay_path)


def tts_cancel_order(request, order_no):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'method_not_allowed'}, status=405)

    order = get_object_or_404(TTSOrder, order_no=order_no)
    email = request.POST.get('email', '').strip()
    if not _can_access_order(request, order, email=email):
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)

    updated, result = _cancel_tts_order(order)
    status_map = {
        'already_cancelled': '任务已取消',
        'already_delivered': '订单已交付，不能取消',
        'cancelled': '任务已取消，额度已退回',
        'force_cancelled': '任务已强制取消，额度已退回',
        'not_cancellable': '当前状态不能取消',
    }
    return JsonResponse(
        {
            'ok': result in {'already_cancelled', 'cancelled', 'force_cancelled'},
            'result': result,
            'message': status_map[result],
            'status': updated.status,
            'cancel_requested': updated.cancel_requested,
        },
        status=200 if result in {'already_cancelled', 'cancelled', 'force_cancelled'} else 400,
    )


def tts_regenerate_order(request, order_no):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'method_not_allowed'}, status=405)

    order = get_object_or_404(TTSOrder, order_no=order_no)
    email = request.POST.get('email', '').strip()
    if not _can_access_order(request, order, email=email):
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)
    if not order.user_id:
        return JsonResponse({'ok': False, 'error': 'user_required', 'message': '当前订单不支持重新生成。'}, status=400)
    if order.status not in {TTSOrder.Status.DELIVERED, TTSOrder.Status.CANCELLED}:
        return JsonResponse({'ok': False, 'error': 'not_ready', 'message': '当前状态暂不支持重新生成。'}, status=400)

    new_order, result = _create_regenerated_tts_order(order.user, order)
    if result == 'insufficient_quota':
        return JsonResponse({'ok': False, 'error': 'insufficient_quota', 'message': '当前额度不足，不能重新生成这条订单。'}, status=400)
    if result == 'empty_source':
        return JsonResponse({'ok': False, 'error': 'empty_source', 'message': '原订单没有可重新生成的文本内容。'}, status=400)
    if new_order is None:
        return JsonResponse({'ok': False, 'error': 'unknown_error', 'message': '重新生成失败。'}, status=500)

    return JsonResponse(
        {
            'ok': True,
            'message': f'已创建新的重新生成订单 {new_order.order_no}',
            'new_order_no': new_order.order_no,
            'redirect_url': f'/tts-studio/submitted/{new_order.order_no}/',
        }
    )


def tts_upload_payment_proof(request, order_no):
    """上传支付截图"""
    order = get_object_or_404(TTSOrder, order_no=order_no)
    if request.method != 'POST':
        return redirect('tts_order_submitted', order_no=order_no)

    form = TTSPaymentProofForm(request.POST, request.FILES, instance=order)
    if form.is_valid():
        updated = form.save(commit=False)
        updated.payment_proof_uploaded_at = timezone.now()
        if updated.payment_status == TTSOrder.PaymentStatus.UNPAID:
            updated.processing_log = f'{timezone.now():%F %T} 用户已上传付款截图，待审核'
        updated.save()
    return redirect('tts_order_submitted', order_no=order_no)


def _verify_payment_webhook_secret(request):
    expected_secret = os.getenv('TTS_PAYMENT_WEBHOOK_SECRET', '').strip()
    if not expected_secret:
        return False
    provided_secret = request.headers.get('X-TTS-Webhook-Secret', '').strip()
    return provided_secret == expected_secret


def _mark_order_paid(order, provider, amount, payment_reference, payload):
    order.payment_status = TTSOrder.PaymentStatus.PAID
    order.payment_provider = provider
    order.payment_reference = payment_reference
    order.payment_callback_payload = payload
    order.status = TTSOrder.Status.QUEUED
    now = timezone.now()
    order.paid_at = order.paid_at or now
    order.payment_verified_at = now
    order.processing_log = (
        f'{now:%F %T} 自动核验到账成功，渠道={provider}，金额={amount}，流水号={payment_reference}'
    )
    order.save()
    transaction.on_commit(lambda: trigger_tts_generation(order.order_no))


@csrf_exempt
def tts_payment_webhook(request, provider):
    """接收支付宝/微信支付回调，自动核验订单金额后入队。"""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'method_not_allowed'}, status=405)

    if provider not in {TTSOrder.PaymentProvider.ALIPAY, TTSOrder.PaymentProvider.WECHAT}:
        return JsonResponse({'ok': False, 'error': 'unsupported_provider'}, status=404)

    if not _verify_payment_webhook_secret(request):
        return JsonResponse({'ok': False, 'error': 'invalid_secret'}, status=403)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'invalid_json'}, status=400)

    order_no = str(payload.get('order_no', '')).strip()
    payment_note_token = str(payload.get('payment_note_token', '')).strip().upper()
    payment_reference = str(payload.get('payment_reference', '')).strip()
    status = str(payload.get('status', '')).strip().lower()

    if (not order_no and not payment_note_token) or not payment_reference:
        return JsonResponse({'ok': False, 'error': 'missing_fields'}, status=400)
    if status not in {'success', 'succeeded', 'paid'}:
        return JsonResponse({'ok': True, 'ignored': True, 'reason': 'non_success_status'})

    try:
        amount = Decimal(str(payload.get('amount', '')).strip())
    except (InvalidOperation, ValueError):
        return JsonResponse({'ok': False, 'error': 'invalid_amount'}, status=400)

    recharge_order = None
    tts_order = None

    if order_no.startswith('TTSR'):
        recharge_order = get_object_or_404(TTSCreditRechargeOrder, order_no=order_no)
    elif order_no.startswith('TTS'):
        tts_order = get_object_or_404(TTSOrder, order_no=order_no)
    elif payment_note_token:
        recharge_order = TTSCreditRechargeOrder.objects.filter(payment_note_token=payment_note_token).first()
        if recharge_order is None:
            tts_order = get_object_or_404(TTSOrder, payment_note_token=payment_note_token)

    if recharge_order is not None:
        expected_amount = recharge_order.amount
        if amount != expected_amount:
            return JsonResponse(
                {
                    'ok': False,
                    'error': 'amount_mismatch',
                    'expected_amount': f'{expected_amount:.2f}',
                    'received_amount': f'{amount:.2f}',
                },
                status=400,
            )
        if recharge_order.payment_status == TTSCreditRechargeOrder.PaymentStatus.PAID:
            return JsonResponse({'ok': True, 'duplicate': True, 'order_no': recharge_order.order_no})
        _apply_recharge_order(recharge_order, provider, amount, payment_reference, payload)
        return JsonResponse({'ok': True, 'order_no': recharge_order.order_no, 'credited_chars': recharge_order.char_amount})

    order = tts_order
    expected_amount = order.payable_amount
    if amount != expected_amount:
        return JsonResponse(
            {
                'ok': False,
                'error': 'amount_mismatch',
                'expected_amount': f'{expected_amount:.2f}',
                'received_amount': f'{amount:.2f}',
            },
            status=400,
        )

    if order.payment_status == TTSOrder.PaymentStatus.PAID:
        return JsonResponse({'ok': True, 'duplicate': True, 'order_no': order.order_no})

    _mark_order_paid(order, provider, amount, payment_reference, payload)
    return JsonResponse({'ok': True, 'order_no': order.order_no, 'next_status': order.status})




def home(request):
    """首页视图"""
    search_query = request.GET.get('q', '').strip()
    search_results = None

    if search_query:
        search_results = Tool.objects.filter(
            Q(name__icontains=search_query) |
            Q(short_description__icontains=search_query) |
            Q(full_description__icontains=search_query),
            is_published=True
        )

    featured_tools = Tool.objects.filter(is_published=True, is_featured=True)[:12]
    recent_tools = Tool.objects.filter(is_published=True)[:6]
    hot_tools = Tool.objects.filter(is_published=True).order_by('-view_count')[:6]

    # 每日推荐：基于日期选择工具
    tools = Tool.objects.filter(is_published=True)
    if tools.exists():
        day_index = date.today().toordinal() % tools.count()
        daily_tool = tools[day_index]
    else:
        daily_tool = None

    categories = Category.objects.all()
    featured_categories = (
        Category.objects
        .filter(tools__is_published=True, tools__is_featured=True)
        .annotate(
            featured_count=Count(
                'tools',
                filter=Q(tools__is_published=True, tools__is_featured=True),
                distinct=True,
            )
        )
        .order_by('-featured_count', 'name')
    )
    featured_topics = TopicPage.objects.filter(is_published=True)[:6]
    tool_count = Tool.objects.filter(is_published=True).count()
    category_count = categories.count()
    column_leaderboard, column_start_date = _get_column_leaderboard()
    column_stats_by_key = {item['page_key']: item for item in column_leaderboard}

    context = {
        'featured_tools': featured_tools,
        'recent_tools': recent_tools,
        'hot_tools': hot_tools,
        'daily_tool': daily_tool,
        'categories': categories,
        'featured_categories': featured_categories,
        'tool_count': tool_count,
        'category_count': category_count,
        'featured_topics': featured_topics,
        'column_leaderboard': column_leaderboard,
        'column_start_date': column_start_date,
        'column_stats_by_key': column_stats_by_key,
        'search_query': search_query,
        'search_results': search_results,
        'today': date.today(),
    }
    response = render(request, 'tools/home.html', context)
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


def openclaw_column(request):
    """OpenClaw 专栏列表页"""
    return render(request, 'tools/openclaw_column.html')


def tool_list(request):
    """工具列表视图"""
    tools = Tool.objects.filter(is_published=True)
    categories = Category.objects.all()
    selected_category = request.GET.get('category')

    if selected_category:
        tools = tools.filter(category__slug=selected_category)

    context = {
        'tools': tools,
        'categories': categories,
        'selected_category': selected_category,
    }
    return render(request, 'tools/tool_list.html', context)


def tool_detail(request, slug):
    """工具详情视图"""
    tool = get_object_or_404(Tool, slug=slug, is_published=True)
    tool.view_count += 1
    tool.save(update_fields=['view_count'])

    daily_view, _ = ToolDailyView.objects.get_or_create(
        tool=tool,
        date=timezone.localdate(),
        defaults={'count': 0}
    )
    daily_view.count += 1
    daily_view.save(update_fields=['count', 'updated_at'])

    same_category_tools = Tool.objects.filter(
        category=tool.category,
        is_published=True
    ).exclude(id=tool.id)[:6]
    hot_tools = Tool.objects.filter(is_published=True).exclude(id=tool.id).order_by('-view_count')[:6]
    featured_tools = Tool.objects.filter(is_published=True, is_featured=True).exclude(id=tool.id)[:6]

    recommended_tools = []
    seen_ids = {tool.id}
    for queryset in (same_category_tools, hot_tools, featured_tools):
        for item in queryset:
            if item.id in seen_ids:
                continue
            recommended_tools.append(item)
            seen_ids.add(item.id)
            if len(recommended_tools) >= 9:
                break
        if len(recommended_tools) >= 9:
            break

    context = {
        'tool': tool,
        'tool_full_description_html': markdown.markdown(
            tool.full_description or '',
            extensions=['extra', 'nl2br', 'sane_lists'],
        ),
        'related_tools': recommended_tools,
    }
    return render(request, 'tools/tool_detail.html', context)


def topic_list(request):
    """专题页列表"""
    topics = TopicPage.objects.filter(is_published=True).prefetch_related('categories')
    suffix_map = {
        "入门指南": "starter",
        "高效工作流": "workflow",
        "免费可用": "free",
    }
    grouped = {}

    for topic in topics:
        category_name = "未分类"
        if topic.categories.exists():
            category_name = topic.categories.first().name

        intent_key = None
        for suffix, mapped in suffix_map.items():
            if suffix in topic.title:
                intent_key = mapped
                break
        if intent_key is None:
            intent_key = "other"

        if category_name not in grouped:
            grouped[category_name] = {
                "category_name": category_name,
                "meta_description": topic.meta_description,
                "intents": {},
                "latest_updated_at": topic.updated_at,
            }
        grouped[category_name]["intents"][intent_key] = topic
        if topic.updated_at > grouped[category_name]["latest_updated_at"]:
            grouped[category_name]["latest_updated_at"] = topic.updated_at

    grouped_topics = sorted(
        grouped.values(),
        key=lambda item: item["latest_updated_at"],
        reverse=True
    )

    for item in grouped_topics:
        item["topic_links"] = []
        if "starter" in item["intents"]:
            item["topic_links"].append(("入门指南", item["intents"]["starter"]))
        if "workflow" in item["intents"]:
            item["topic_links"].append(("高效工作流", item["intents"]["workflow"]))
        if "free" in item["intents"]:
            item["topic_links"].append(("免费可用", item["intents"]["free"]))
        for key, topic in item["intents"].items():
            if key not in {"starter", "workflow", "free"}:
                item["topic_links"].append(("更多专题", topic))

    context = {
        'topics': topics,
        'grouped_topics': grouped_topics,
    }
    return render(request, 'tools/topic_list.html', context)


def topic_detail(request, slug):
    """专题详情页"""
    topic = get_object_or_404(TopicPage, slug=slug, is_published=True)
    tools = Tool.objects.filter(
        is_published=True,
        category__in=topic.categories.all()
    ).distinct().order_by('-view_count', '-created_at')
    categories = topic.categories.all()
    related_topics = TopicPage.objects.filter(
        is_published=True,
        categories__in=categories
    ).exclude(id=topic.id).distinct()[:6]

    context = {
        'topic': topic,
        'tools': tools[:60],
        'related_topics': related_topics,
        'categories': categories,
    }
    return render(request, 'tools/topic_detail.html', context)


def trending_tools(request):
    """7日热度榜"""
    start_date = timezone.localdate() - timedelta(days=6)
    trending = (
        Tool.objects.filter(is_published=True)
        .annotate(
            week_views=Sum(
                'daily_views__count',
                filter=Q(daily_views__date__gte=start_date)
            )
        )
        .order_by('-week_views', '-view_count', '-created_at')[:100]
    )

    context = {
        'trending_tools': trending,
        'start_date': start_date,
        'end_date': timezone.localdate(),
    }
    return render(request, 'tools/trending_tools.html', context)


def trending_columns(request):
    """7日专栏热度榜"""
    column_leaderboard, start_date = _get_column_leaderboard()
    context = {
        'trending_columns': column_leaderboard,
        'start_date': start_date,
        'end_date': timezone.localdate(),
    }
    return render(request, 'tools/trending_columns.html', context)


def robots_txt(request):
    """robots.txt视图"""
    lines = [
        "User-agent: *",
        "Allow: /",
        "Sitemap: {}/sitemap.xml".format(request.build_absolute_uri('/').rstrip('/')),
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")


def indexnow_key_txt(request, key):
    """IndexNow key 文件，按根路径提供: /<key>.txt"""
    key_path = settings.BASE_DIR / f"{key}.txt"
    if not key_path.exists():
        raise Http404("Key file not found")
    return HttpResponse(key_path.read_text(encoding='utf-8'), content_type="text/plain")
