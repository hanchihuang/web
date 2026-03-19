import os
import json
import subprocess
import sys
import re
from decimal import Decimal, InvalidOperation
from base64 import b64encode
from io import BytesIO

from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, Http404, JsonResponse
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from datetime import date, timedelta
from .forms import (
    TTSOrderForm,
    TTSOrderLookupForm,
    TTSPaymentProofForm,
    TTSCreditLoginForm,
    TTSCreditRegisterForm,
    TTSRechargeForm,
    TTSCreditConsumeForm,
    TTSCreditRechargeProofForm,
)
from .models import Category, Tool, TopicPage, ColumnPage, ColumnDailyView, ToolDailyView, TTSOrder, TTSCreditAccount, TTSCreditRechargeOrder, TTSCreditLedger
from .tts_config import get_tts_runtime_rules, estimate_total_chunks
from .tts import VOICE_PRESET_CONFIG, build_quote, build_turnaround, build_recharge_amount, DEFAULT_RECHARGE_PACKS
from .tts_jobs import stop_tts_worker, trigger_tts_generation
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


def quant_article_tardis(request):
    """量化资源专栏文章：TARDIS 数据指南"""
    return render(request, 'tools/quant_article_tardis.html')


def quant_article_tushare(request):
    """量化资源专栏文章：Tushare Pro 数据权限说明"""
    return render(request, 'tools/quant_article_tushare.html')


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


def _get_credit_account(user):
    return TTSCreditAccount.objects.get_or_create(user=user)[0]


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
        message = '已进入队列，等待 GPU 开始生成'
    elif order.status == TTSOrder.Status.GENERATING:
        progress = max(progress, 15)
        if message == '等待处理' or not matched:
            message = '正在加载模型并开始生成'
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


def tts_studio(request):
    """TTS 额度充值与消费页面"""
    login_form, register_form = _build_auth_forms()
    recharge_form = TTSRechargeForm()
    consume_form = TTSCreditConsumeForm(
        initial={
            'voice_preset': TTSOrder.VoicePreset.VIVIAN,
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
    recent_orders = request.user.tts_orders.order_by('-created_at')[:10] if request.user.is_authenticated else []
    recent_recharges = request.user.tts_recharge_orders.order_by('-created_at')[:10] if request.user.is_authenticated else []
    context = {
        'login_form': login_form,
        'register_form': register_form,
        'recharge_form': recharge_form,
        'consume_form': consume_form,
        'voice_cards': voice_cards,
        'pricing_examples': DEFAULT_RECHARGE_PACKS,
        'sales_wechat': os.getenv('TTS_SALES_WECHAT', 'dreamsjtuai'),
        'payment_note': os.getenv('TTS_PAYMENT_NOTE', '登录后直接购买字数额度，微信支付回调自动充值到账；提交文本时自动扣额度并入队生成。'),
        'manual_payment_notice': MANUAL_PAYMENT_NOTICE,
        'auth_error': auth_error,
        'account': account,
        'recent_orders': recent_orders,
        'recent_recharges': recent_recharges,
    }
    return render(request, 'tools/tts_studio.html', context)


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
    tier_name = build_quote(order.char_count, order.business_usage)[1]
    context = {
        'order': order,
        'order_progress': _build_order_progress(order),
        'order_elapsed': _build_order_elapsed(order),
        'tier_name': tier_name,
        'turnaround': build_turnaround(order.char_count),
        'sales_wechat': os.getenv('TTS_SALES_WECHAT', 'dreamsjtuai'),
        'payment_note': os.getenv('TTS_PAYMENT_NOTE', '额度已扣减，订单已直接进入生成队列。'),
        'proof_form': TTSPaymentProofForm(instance=order),
        'manual_payment_notice': MANUAL_PAYMENT_NOTICE,
        'can_cancel': order.status in {TTSOrder.Status.QUEUED, TTSOrder.Status.GENERATING},
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
            proof_form = TTSPaymentProofForm(instance=order)

    context = {
        'form': form,
        'order': order,
        'order_progress': _build_order_progress(order) if order else None,
        'order_elapsed': _build_order_elapsed(order) if order else None,
        'proof_form': proof_form,
        'sales_wechat': os.getenv('TTS_SALES_WECHAT', 'dreamsjtuai'),
        'manual_payment_notice': MANUAL_PAYMENT_NOTICE,
        'can_cancel': bool(order and order.status in {TTSOrder.Status.QUEUED, TTSOrder.Status.GENERATING}),
    }
    return render(request, 'tools/tts_order_query.html', context)


def tts_order_status(request, order_no):
    order = get_object_or_404(TTSOrder, order_no=order_no)
    email = request.GET.get('email', '').strip()
    if not _can_access_order(request, order, email=email):
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)

    progress = _build_order_progress(order)
    return JsonResponse(
        {
            'ok': True,
            'order_no': order.order_no,
            'status': order.status,
            'status_display': order.get_status_display(),
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
            'output_file_url': '' if not order.output_file or order.is_output_expired else order.output_file.url,
            'output_expires_at': order.output_expires_at.isoformat() if order.output_expires_at else '',
            'is_output_expired': order.is_output_expired,
        }
    )


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
