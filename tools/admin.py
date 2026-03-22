from django.contrib import admin, messages
from django.http import HttpResponse
from django.utils import timezone

from .models import Category, Tool, TopicPage, ToolDailyView, TTSOrder, TTSCreditAccount, TTSCreditRechargeOrder, TTSCreditLedger, ApiRelayService, UserApiRelayAccess, TardisRagEntry, TushareRagEntry, EdgeInferenceOffer, EdgeInferenceRequest
from .tts_jobs import trigger_tts_generation


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'created_at']
    search_fields = ['name', 'description']
    prepopulated_fields = {'slug': ('name',)}
    ordering = ['name']


@admin.register(Tool)
class ToolAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'is_featured', 'is_published', 'created_at']
    list_filter = ['category', 'is_featured', 'is_published', 'created_at']
    search_fields = ['name', 'short_description', 'full_description']
    prepopulated_fields = {'slug': ('name',)}
    list_editable = ['is_featured', 'is_published']
    ordering = ['-created_at']


@admin.register(TopicPage)
class TopicPageAdmin(admin.ModelAdmin):
    list_display = ['title', 'slug', 'is_published', 'updated_at']
    search_fields = ['title', 'meta_description', 'intro']
    prepopulated_fields = {'slug': ('title',)}
    list_filter = ['is_published', 'updated_at']
    filter_horizontal = ['categories']
    ordering = ['-updated_at']


@admin.register(ToolDailyView)
class ToolDailyViewAdmin(admin.ModelAdmin):
    list_display = ['tool', 'date', 'count', 'updated_at']
    search_fields = ['tool__name']
    list_filter = ['date']
    ordering = ['-date', '-count']


@admin.action(description='标记为已付款并进入待生成队列')
def mark_paid_and_queue(modeladmin, request, queryset):
    now = timezone.now()
    order_nos = list(queryset.values_list('order_no', flat=True))
    queryset.update(
        payment_status=TTSOrder.PaymentStatus.PAID,
        status=TTSOrder.Status.QUEUED,
        payment_verified_at=now,
        paid_at=now,
    )
    for order_no in order_nos:
        trigger_tts_generation(order_no)


@admin.register(TTSOrder)
class TTSOrderAdmin(admin.ModelAdmin):
    list_display = [
        'order_no', 'contact_name', 'char_count', 'estimated_price', 'payment_provider',
        'payment_reference', 'payment_status', 'status', 'created_at'
    ]
    list_filter = ['voice_preset', 'payment_status', 'status', 'business_usage', 'delivery_format']
    search_fields = ['order_no', 'contact_name', 'email', 'wechat', 'company', 'source_text']
    readonly_fields = [
        'order_no', 'char_count', 'estimated_price', 'payment_note_token', 'payment_reference',
        'payment_verified_at', 'output_duration_seconds', 'payment_proof_uploaded_at', 'created_at', 'updated_at'
    ]
    actions = [mark_paid_and_queue]
    ordering = ['-created_at']


@admin.register(TTSCreditAccount)
class TTSCreditAccountAdmin(admin.ModelAdmin):
    list_display = ['user', 'is_unlimited', 'char_balance', 'total_purchased_chars', 'total_used_chars', 'updated_at']
    search_fields = ['user__username', 'user__email']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(TTSCreditRechargeOrder)
class TTSCreditRechargeOrderAdmin(admin.ModelAdmin):
    list_display = ['order_no', 'user', 'char_amount', 'amount', 'payment_status', 'payment_provider', 'created_at']
    list_filter = ['payment_status', 'payment_provider', 'created_at']
    search_fields = ['order_no', 'user__username', 'user__email', 'payment_reference', 'payment_note_token']
    readonly_fields = ['order_no', 'payment_note_token', 'created_at', 'updated_at', 'paid_at', 'payment_verified_at']

    @admin.action(description='确认到账并自动发放字数额度')
    def confirm_recharge(self, request, queryset):
        confirmed = 0
        for order in queryset:
            if order.payment_status == TTSCreditRechargeOrder.PaymentStatus.PAID:
                continue

            account, _ = TTSCreditAccount.objects.get_or_create(user=order.user)
            account.char_balance += order.char_amount
            account.total_purchased_chars += order.char_amount
            account.save(update_fields=['char_balance', 'total_purchased_chars', 'updated_at'])

            TTSCreditLedger.objects.create(
                user=order.user,
                entry_type=TTSCreditLedger.EntryType.RECHARGE,
                char_delta=order.char_amount,
                balance_after=account.char_balance,
                recharge_order=order,
                note=f'后台确认到账，发放 {order.char_amount} 字',
            )

            order.payment_status = TTSCreditRechargeOrder.PaymentStatus.PAID
            order.payment_provider = TTSCreditRechargeOrder.PaymentProvider.WECHAT
            order.paid_at = timezone.now()
            order.payment_verified_at = timezone.now()
            order.save(update_fields=['payment_status', 'payment_provider', 'paid_at', 'payment_verified_at', 'updated_at'])
            confirmed += 1

        self.message_user(request, f'已确认 {confirmed} 笔充值到账。', level=messages.SUCCESS)

    actions = ['confirm_recharge']


@admin.register(TTSCreditLedger)
class TTSCreditLedgerAdmin(admin.ModelAdmin):
    list_display = ['user', 'entry_type', 'char_delta', 'balance_after', 'created_at']
    list_filter = ['entry_type', 'created_at']
    search_fields = ['user__username', 'note']
    readonly_fields = ['created_at']


@admin.register(ApiRelayService)
class ApiRelayServiceAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'public_path', 'apply_url', 'base_url', 'is_active', 'require_api_key', 'require_login', 'require_manual_approval', 'updated_at']
    list_filter = ['is_active', 'require_api_key', 'require_login', 'require_manual_approval', 'updated_at']
    search_fields = ['name', 'slug', 'base_url', 'public_path', 'apply_url', 'description', 'note']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(UserApiRelayAccess)
class UserApiRelayAccessAdmin(admin.ModelAdmin):
    list_display = ['user', 'service', 'is_enabled', 'api_key_prefix', 'api_key_last4', 'api_key_created_at', 'approved_at', 'expires_at', 'updated_at']
    list_filter = ['service', 'is_enabled', 'approved_at', 'expires_at']
    search_fields = ['user__username', 'user__email', 'service__slug', 'service__name', 'note']
    readonly_fields = ['api_key_prefix', 'api_key_last4', 'api_key_created_at', 'created_at', 'updated_at']

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.is_superuser:
            actions.pop('generate_api_keys', None)
            actions.pop('revoke_api_keys', None)
        return actions

    @admin.action(description='超级管理员生成 API Key')
    def generate_api_keys(self, request, queryset):
        if not request.user.is_superuser:
            self.message_user(request, '只有超级管理员可以生成 API Key。', level=messages.ERROR)
            return None
        lines = ['username,service,api_key']
        for access in queryset.select_related('user', 'service'):
            raw_key = access.issue_api_key()
            access.save(update_fields=['api_key_prefix', 'api_key_secret_hash', 'api_key_last4', 'api_key_created_at', 'updated_at'])
            lines.append(f'{access.user.username},{access.service.slug},{raw_key}')
        response = HttpResponse('\n'.join(lines), content_type='text/plain; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="api_keys.txt"'
        return response

    @admin.action(description='超级管理员吊销 API Key')
    def revoke_api_keys(self, request, queryset):
        if not request.user.is_superuser:
            self.message_user(request, '只有超级管理员可以吊销 API Key。', level=messages.ERROR)
            return None
        updated = 0
        for access in queryset:
            access.revoke_api_key()
            access.save(update_fields=['api_key_prefix', 'api_key_secret_hash', 'api_key_last4', 'api_key_created_at', 'updated_at'])
            updated += 1
        self.message_user(request, f'已吊销 {updated} 个 API Key。', level=messages.SUCCESS)

    @admin.action(description='批量开通 API 访问权限')
    def enable_access(self, request, queryset):
        now = timezone.now()
        updated = queryset.update(is_enabled=True, approved_at=now)
        self.message_user(request, f'已开通 {updated} 条 API 访问权限。', level=messages.SUCCESS)

    actions = ['enable_access', 'approve_selected_accesses', 'disable_selected_accesses', 'generate_api_keys', 'revoke_api_keys']

    @admin.action(description='开通所选用户 API 访问权限')
    def approve_selected_accesses(self, request, queryset):
        updated = 0
        now = timezone.now()
        for access in queryset:
            access.is_enabled = True
            access.approved_at = access.approved_at or now
            access.save(update_fields=['is_enabled', 'approved_at', 'updated_at'])
            updated += 1
        self.message_user(request, f'已开通 {updated} 条 API 访问权限。')

    @admin.action(description='关闭所选用户 API 访问权限')
    def disable_selected_accesses(self, request, queryset):
        updated = queryset.update(is_enabled=False, updated_at=timezone.now())
        self.message_user(request, f'已关闭 {updated} 条 API 访问权限。')


@admin.register(TardisRagEntry)
class TardisRagEntryAdmin(admin.ModelAdmin):
    list_display = ['title', 'question_hint', 'sort_order', 'is_active', 'updated_at']
    list_filter = ['is_active', 'updated_at']
    search_fields = ['title', 'question_hint', 'answer', 'keywords']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(TushareRagEntry)
class TushareRagEntryAdmin(admin.ModelAdmin):
    list_display = ['title', 'question_hint', 'sort_order', 'is_active', 'updated_at']
    list_filter = ['is_active', 'updated_at']
    search_fields = ['title', 'question_hint', 'answer', 'keywords']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(EdgeInferenceOffer)
class EdgeInferenceOfferAdmin(admin.ModelAdmin):
    list_display = ['name', 'provider', 'gpu_name', 'gpu_count', 'vram_gb', 'price', 'billing_unit', 'relay_service', 'stock', 'is_active', 'sort_order']
    list_filter = ['billing_unit', 'is_active', 'provider', 'gpu_name', 'relay_service']
    search_fields = ['name', 'provider', 'gpu_name', 'region', 'supported_models', 'endpoint_protocols', 'relay_service__slug', 'relay_service__name']
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ['created_at', 'updated_at']


@admin.register(EdgeInferenceRequest)
class EdgeInferenceRequestAdmin(admin.ModelAdmin):
    list_display = ['contact_name', 'email', 'offer', 'requested_model', 'expected_concurrency', 'expected_hours', 'budget', 'status', 'public_endpoint', 'created_at']
    list_filter = ['status', 'offer', 'created_at']
    search_fields = ['contact_name', 'email', 'wechat', 'requested_model', 'use_case', 'admin_note']
    readonly_fields = ['api_key_prefix', 'api_key_last4', 'api_key_created_at', 'created_at', 'updated_at']
    actions = ['approve_and_provision', 'mark_active', 'mark_completed']

    @admin.action(description='批准并生成访问凭证')
    def approve_and_provision(self, request, queryset):
        lines = ['request_id,email,status,endpoint,api_key,ssh']
        for item in queryset.select_related('offer', 'offer__relay_service', 'user'):
            raw_key = ''
            relay_service = item.offer.relay_service if item.offer and item.offer.relay_service_id else None
            if item.user_id and relay_service:
                access, _ = UserApiRelayAccess.objects.get_or_create(user=item.user, service=relay_service)
                access.is_enabled = True
                access.approved_at = access.approved_at or timezone.now()
                raw_key = access.issue_api_key()
                access.note = access.note or f'由边缘推理请求 #{item.id} 自动开通'
                access.save(update_fields=[
                    'is_enabled', 'approved_at', 'api_key_prefix', 'api_key_secret_hash',
                    'api_key_last4', 'api_key_created_at', 'note', 'updated_at',
                ])
                item.apply_relay_access(access)
            else:
                raw_key = item.issue_access_key()
            item.status = EdgeInferenceRequest.Status.APPROVED
            item.activated_at = timezone.now()
            if not item.public_endpoint:
                offer_slug = item.offer.slug if item.offer else 'edge'
                item.public_endpoint = f'https://ai-tool.indevs.in/api-relay/{offer_slug}/'
            if not item.ssh_host:
                item.ssh_host = 'ai-tool.indevs.in'
            if not item.ssh_username:
                item.ssh_username = 'user'
            if not item.access_note:
                if relay_service and item.user_id:
                    item.access_note = '已自动绑定站内 API Relay 权限。请使用本次发放的 API Key 调用同域入口。'
                else:
                    item.access_note = '默认发放 API 入口与 SSH 占位信息。后续可替换成真实节点接入信息。'
            item.save(update_fields=[
                'status', 'activated_at', 'public_endpoint', 'api_key_prefix', 'api_key_secret_hash',
                'api_key_last4', 'api_key_created_at', 'ssh_host', 'ssh_port', 'ssh_username', 'access_note', 'updated_at'
            ])
            ssh_line = f'ssh {item.ssh_username}@{item.ssh_host} -p {item.ssh_port}'
            lines.append(f'{item.id},{item.email},{item.status},{item.public_endpoint},{raw_key},{ssh_line}')
        response = HttpResponse('\n'.join(lines), content_type='text/plain; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="edge_inference_access.txt"'
        return response

    @admin.action(description='标记为运行中')
    def mark_active(self, request, queryset):
        updated = queryset.update(status=EdgeInferenceRequest.Status.ACTIVE, updated_at=timezone.now())
        self.message_user(request, f'已标记 {updated} 条请求为运行中。', level=messages.SUCCESS)

    @admin.action(description='标记为已结束')
    def mark_completed(self, request, queryset):
        updated = queryset.update(status=EdgeInferenceRequest.Status.COMPLETED, updated_at=timezone.now())
        self.message_user(request, f'已标记 {updated} 条请求为已结束。', level=messages.SUCCESS)
