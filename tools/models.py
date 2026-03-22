import json

from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify
from django.utils import timezone
from decimal import Decimal
import secrets


class Category(models.Model):
    """工具分类模型"""
    name = models.CharField('分类名称', max_length=100, unique=True)
    slug = models.SlugField('URL标识', max_length=100, unique=True, blank=True)
    description = models.TextField('分类描述', blank=True)
    url = models.URLField('分类链接', blank=True)
    order = models.IntegerField('排序', default=0)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        app_label = 'tools'
        verbose_name = '分类'
        verbose_name_plural = '分类'
        ordering = ['order', 'name']

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name, allow_unicode=True)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Tool(models.Model):
    """工具模型"""
    name = models.CharField('工具名称', max_length=200)
    slug = models.SlugField('URL标识', max_length=200, unique=True, blank=True)
    short_description = models.CharField('简短描述', max_length=300)
    full_description = models.TextField('完整描述')
    website_url = models.URLField('工具网站')
    logo = models.ImageField('Logo', upload_to='tool_logos/', blank=True, null=True)
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name='tools',
        verbose_name='分类'
    )
    is_featured = models.BooleanField('是否精选', default=False)
    is_published = models.BooleanField('是否发布', default=True)
    view_count = models.IntegerField('浏览次数', default=0)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        app_label = 'tools'
        verbose_name = '工具'
        verbose_name_plural = '工具'
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name, allow_unicode=True)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class TopicPage(models.Model):
    """SEO专题页，用于程序化生成长尾流量页面"""
    title = models.CharField('专题标题', max_length=200)
    slug = models.SlugField('URL标识', max_length=200, unique=True)
    meta_description = models.CharField('SEO描述', max_length=300)
    intro = models.TextField('专题介绍', blank=True)
    categories = models.ManyToManyField(
        Category,
        related_name='topic_pages',
        verbose_name='关联分类',
        blank=True,
    )
    is_published = models.BooleanField('是否发布', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        app_label = 'tools'
        verbose_name = '专题页'
        verbose_name_plural = '专题页'
        ordering = ['-updated_at']

    def __str__(self):
        return self.title


class ColumnPage(models.Model):
    """静态专栏页统计"""
    page_key = models.CharField('页面标识', max_length=80, unique=True)
    title = models.CharField('页面标题', max_length=200)
    path = models.CharField('页面路径', max_length=255)
    view_count = models.PositiveIntegerField('浏览次数', default=0)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        app_label = 'tools'
        verbose_name = '专栏页'
        verbose_name_plural = '专栏页'
        ordering = ['-view_count', 'title']

    def __str__(self):
        return self.title


class ColumnDailyView(models.Model):
    """按天统计专栏页热度"""
    column = models.ForeignKey(
        ColumnPage,
        on_delete=models.CASCADE,
        related_name='daily_views',
        verbose_name='专栏页'
    )
    date = models.DateField('日期', default=timezone.localdate)
    count = models.PositiveIntegerField('浏览量', default=0)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        app_label = 'tools'
        verbose_name = '专栏页日浏览'
        verbose_name_plural = '专栏页日浏览'
        unique_together = ('column', 'date')
        ordering = ['-date', '-count']

    def __str__(self):
        return f"{self.column.title} - {self.date} ({self.count})"


class ToolDailyView(models.Model):
    """按天统计工具热度，用于趋势榜单"""
    tool = models.ForeignKey(
        Tool,
        on_delete=models.CASCADE,
        related_name='daily_views',
        verbose_name='工具'
    )
    date = models.DateField('日期', default=timezone.localdate)
    count = models.PositiveIntegerField('浏览量', default=0)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        app_label = 'tools'
        verbose_name = '工具日浏览'
        verbose_name_plural = '工具日浏览'
        unique_together = ('tool', 'date')
        ordering = ['-date', '-count']

    def __str__(self):
        return f"{self.tool.name} - {self.date} ({self.count})"


class TTSOrder(models.Model):
    """商业化 TTS 订单"""

    class Status(models.TextChoices):
        PENDING_PAYMENT = 'pending_payment', '待付款'
        QUEUED = 'queued', '待生成'
        GENERATING = 'generating', '生成中'
        DELIVERED = 'delivered', '已交付'
        CANCELLED = 'cancelled', '已取消'

    class PaymentStatus(models.TextChoices):
        UNPAID = 'unpaid', '未付款'
        PAID = 'paid', '已付款'
        REFUNDED = 'refunded', '已退款'

    class PaymentProvider(models.TextChoices):
        ALIPAY = 'alipay', '支付宝'
        WECHAT = 'wechat', '微信支付'

    class VoicePreset(models.TextChoices):
        AIDEN = 'aiden', 'Aiden'
        DYLAN = 'dylan', 'Dylan'
        ERIC = 'eric', 'Eric'
        ONO_ANNA = 'ono_anna', 'Ono_anna'
        RYAN = 'ryan', 'Ryan'
        SERENA = 'serena', 'Serena'
        SOHEE = 'sohee', 'Sohee'
        UNCLE_FU = 'uncle_fu', 'Uncle_fu'
        VIVIAN = 'vivian', 'Vivian'
        SWEET_VIVIAN = 'sweet_vivian', '旧版预设：Vivian'
        GENTLE_SERENA = 'gentle_serena', '旧版预设：Serena'
        YOUTHFUL_ONO = 'youthful_ono', '旧版预设：Ono_anna'
        CUSTOM = 'custom', '旧版预设：定制风格'

    class DeliveryFormat(models.TextChoices):
        MP3 = 'mp3', 'MP3'
        WAV = 'wav', 'WAV'

    order_no = models.CharField('订单号', max_length=32, unique=True, blank=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, blank=True, null=True, related_name='tts_orders', verbose_name='用户')
    contact_name = models.CharField('联系人', max_length=80)
    email = models.EmailField('邮箱')
    wechat = models.CharField('微信/Telegram', max_length=80, blank=True)
    company = models.CharField('公司/品牌', max_length=120, blank=True)
    source_text = models.TextField('待转文本')
    char_count = models.PositiveIntegerField('字数', default=0)
    voice_preset = models.CharField('音色方案', max_length=32, choices=VoicePreset.choices, default=VoicePreset.SERENA)
    style_notes = models.CharField('补充风格要求', max_length=240, blank=True)
    business_usage = models.BooleanField('商业用途授权', default=True)
    delivery_format = models.CharField('交付格式', max_length=8, choices=DeliveryFormat.choices, default=DeliveryFormat.MP3)
    estimated_price = models.DecimalField('系统报价', max_digits=10, decimal_places=2, default=0)
    final_price = models.DecimalField('最终成交价', max_digits=10, decimal_places=2, blank=True, null=True)
    status = models.CharField('订单状态', max_length=24, choices=Status.choices, default=Status.PENDING_PAYMENT)
    payment_status = models.CharField('支付状态', max_length=24, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID)
    payment_provider = models.CharField('支付渠道', max_length=16, choices=PaymentProvider.choices, blank=True)
    payment_reference = models.CharField('渠道流水号', max_length=80, blank=True)
    payment_note_token = models.CharField('付款备注码', max_length=12, unique=True, blank=True)
    payment_callback_payload = models.JSONField('支付回调载荷', blank=True, null=True)
    payment_proof = models.ImageField('支付凭证', upload_to='tts_payment_proofs/', blank=True, null=True)
    payment_proof_uploaded_at = models.DateTimeField('支付凭证上传时间', blank=True, null=True)
    output_file = models.FileField('生成音频', upload_to='tts_orders/', blank=True, null=True)
    output_duration_seconds = models.PositiveIntegerField('音频时长（秒）', blank=True, null=True)
    output_expires_at = models.DateTimeField('音频过期时间', blank=True, null=True)
    cancel_requested = models.BooleanField('已请求取消', default=False)
    processing_log = models.TextField('处理日志', blank=True)
    paid_at = models.DateTimeField('付款时间', blank=True, null=True)
    payment_verified_at = models.DateTimeField('支付核验时间', blank=True, null=True)
    delivered_at = models.DateTimeField('交付时间', blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        app_label = 'tools'
        verbose_name = 'TTS订单'
        verbose_name_plural = 'TTS订单'
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.order_no:
            self.order_no = timezone.now().strftime('TTS%Y%m%d%H%M%S%f')[:-3]
        if not self.payment_note_token:
            self.payment_note_token = secrets.token_hex(3).upper()
        if self.source_text:
            self.char_count = len(self.source_text.strip())
        super().save(*args, **kwargs)

    @property
    def payable_amount(self) -> Decimal:
        return self.final_price if self.final_price is not None else self.estimated_price

    @property
    def is_output_expired(self) -> bool:
        return bool(self.output_expires_at and timezone.now() >= self.output_expires_at)

    def __str__(self):
        return f"{self.order_no} - {self.contact_name}"


class TTSCreditAccount(models.Model):
    """用户 TTS 字数额度账户"""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='tts_credit_account', verbose_name='用户')
    is_unlimited = models.BooleanField('是否无限额度', default=True)
    char_balance = models.PositiveIntegerField('剩余字数额度', default=0)
    total_purchased_chars = models.PositiveIntegerField('累计购买字数', default=0)
    total_used_chars = models.PositiveIntegerField('累计消费字数', default=0)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        app_label = 'tools'
        verbose_name = 'TTS额度账户'
        verbose_name_plural = 'TTS额度账户'

    def __str__(self):
        return f'{self.user.username} - {self.char_balance} chars'


class TTSCreditRechargeOrder(models.Model):
    """字数额度充值订单"""

    class PaymentStatus(models.TextChoices):
        UNPAID = 'unpaid', '未付款'
        PAID = 'paid', '已付款'
        REFUNDED = 'refunded', '已退款'

    class PaymentProvider(models.TextChoices):
        ALIPAY = 'alipay', '支付宝'
        WECHAT = 'wechat', '微信支付'

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tts_recharge_orders', verbose_name='用户')
    order_no = models.CharField('充值订单号', max_length=32, unique=True, blank=True)
    char_amount = models.PositiveIntegerField('充值字数', default=0)
    amount = models.DecimalField('充值金额', max_digits=10, decimal_places=2, default=0)
    payment_status = models.CharField('支付状态', max_length=24, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID)
    payment_provider = models.CharField('支付渠道', max_length=16, choices=PaymentProvider.choices, blank=True)
    payment_reference = models.CharField('渠道流水号', max_length=80, blank=True)
    payment_note_token = models.CharField('付款备注码', max_length=12, unique=True, blank=True)
    payment_callback_payload = models.JSONField('支付回调载荷', blank=True, null=True)
    wechat_code_url = models.TextField('微信支付二维码链接', blank=True)
    wechat_prepay_id = models.CharField('微信预支付ID', max_length=80, blank=True)
    wechat_openid = models.CharField('支付用户OpenID', max_length=64, blank=True)
    payment_error = models.CharField('支付错误信息', max_length=255, blank=True)
    payment_proof = models.ImageField('付款截图', upload_to='tts_recharge_proofs/', blank=True, null=True)
    payment_proof_uploaded_at = models.DateTimeField('付款截图上传时间', blank=True, null=True)
    paid_at = models.DateTimeField('付款时间', blank=True, null=True)
    payment_verified_at = models.DateTimeField('支付核验时间', blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        app_label = 'tools'
        verbose_name = 'TTS充值订单'
        verbose_name_plural = 'TTS充值订单'
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.order_no:
            self.order_no = timezone.now().strftime('TTSR%Y%m%d%H%M%S%f')[:-3]
        if not self.payment_note_token:
            self.payment_note_token = secrets.token_hex(3).upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.order_no} - {self.user.username}'


class TTSCreditLedger(models.Model):
    """额度流水"""

    class EntryType(models.TextChoices):
        RECHARGE = 'recharge', '充值'
        CONSUME = 'consume', '消费'
        REFUND = 'refund', '退款'

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tts_credit_ledgers', verbose_name='用户')
    entry_type = models.CharField('流水类型', max_length=24, choices=EntryType.choices)
    char_delta = models.IntegerField('字数变动')
    balance_after = models.PositiveIntegerField('变动后余额')
    recharge_order = models.ForeignKey(
        TTSCreditRechargeOrder,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='ledger_entries',
        verbose_name='关联充值单',
    )
    tts_order = models.ForeignKey(
        TTSOrder,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='ledger_entries',
        verbose_name='关联TTS订单',
    )
    note = models.CharField('备注', max_length=255, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        app_label = 'tools'
        verbose_name = 'TTS额度流水'
        verbose_name_plural = 'TTS额度流水'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user.username} {self.entry_type} {self.char_delta}'


class ApiRelayService(models.Model):
    """可转接的 API 服务配置"""

    slug = models.SlugField('服务标识', max_length=64, unique=True)
    name = models.CharField('服务名称', max_length=120)
    base_url = models.URLField('上游基础地址')
    is_active = models.BooleanField('是否启用', default=True)
    require_api_key = models.BooleanField('要求 API Key', default=False)
    require_login = models.BooleanField('需要登录', default=True)
    require_manual_approval = models.BooleanField('需要后台授权', default=True)
    allowed_methods = models.CharField('允许方法', max_length=64, default='GET,POST')
    timeout_seconds = models.PositiveIntegerField('请求超时（秒）', default=60)
    upstream_headers = models.TextField('固定上游请求头(JSON)', blank=True, default='')
    upstream_query_params = models.TextField('固定上游查询参数(JSON)', blank=True, default='')
    public_path = models.CharField('对外访问路径', max_length=255, blank=True, default='')
    apply_url = models.CharField('申请/说明页', max_length=255, blank=True, default='')
    description = models.TextField('服务说明', blank=True, default='')
    example_paths = models.TextField('示例路径（每行一个）', blank=True, default='')
    note = models.CharField('备注', max_length=255, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        app_label = 'tools'
        verbose_name = 'API转接服务'
        verbose_name_plural = 'API转接服务'
        ordering = ['name', 'slug']

    def __str__(self):
        return f'{self.name} ({self.slug})'

    @property
    def allowed_method_set(self) -> set[str]:
        return {item.strip().upper() for item in self.allowed_methods.split(',') if item.strip()}

    @property
    def public_url_path(self) -> str:
        if self.public_path:
            return self.public_path.strip()
        if self.slug == 'tushare':
            return '/tushare/'
        return f'/api-relay/{self.slug}/'

    def clean(self):
        super().clean()
        for field_name in ('upstream_headers', 'upstream_query_params'):
            raw_value = (getattr(self, field_name) or '').strip()
            if not raw_value:
                continue
            try:
                parsed = json.loads(raw_value)
            except json.JSONDecodeError as exc:
                raise ValidationError({field_name: f'需要填写合法 JSON 对象: {exc}'})
            if not isinstance(parsed, dict):
                raise ValidationError({field_name: '这里只接受 JSON 对象，例如 {"Authorization": "Bearer xxx"}'})


class UserApiRelayAccess(models.Model):
    """用户对 API 转接服务的访问授权"""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='api_relay_accesses', verbose_name='用户')
    service = models.ForeignKey(ApiRelayService, on_delete=models.CASCADE, related_name='user_accesses', verbose_name='服务')
    is_enabled = models.BooleanField('是否已授权', default=False)
    api_key_prefix = models.CharField('API Key 前缀', max_length=32, unique=True, blank=True, null=True, default=None)
    api_key_secret_hash = models.CharField('API Key 哈希', max_length=255, blank=True, default='')
    api_key_last4 = models.CharField('API Key 后四位', max_length=4, blank=True, default='')
    api_key_created_at = models.DateTimeField('API Key 生成时间', blank=True, null=True)
    approved_at = models.DateTimeField('授权时间', blank=True, null=True)
    expires_at = models.DateTimeField('过期时间', blank=True, null=True)
    note = models.CharField('备注', max_length=255, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        app_label = 'tools'
        verbose_name = '用户 API 访问授权'
        verbose_name_plural = '用户 API 访问授权'
        unique_together = ('user', 'service')
        ordering = ['-updated_at']

    def __str__(self):
        return f'{self.user.username} -> {self.service.slug} ({self.is_enabled})'

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key_prefix and self.api_key_secret_hash)

    def issue_api_key(self) -> str:
        prefix = f'atk_{secrets.token_hex(4)}'
        secret = secrets.token_urlsafe(24)
        self.api_key_prefix = prefix
        self.api_key_secret_hash = make_password(secret)
        self.api_key_last4 = secret[-4:]
        self.api_key_created_at = timezone.now()
        return f'{prefix}.{secret}'

    def revoke_api_key(self):
        self.api_key_prefix = None
        self.api_key_secret_hash = ''
        self.api_key_last4 = ''
        self.api_key_created_at = None


class TardisRagEntry(models.Model):
    """TARDIS 页面客服语料"""

    title = models.CharField('标题', max_length=120)
    question_hint = models.CharField('问题提示', max_length=255, blank=True, default='')
    answer = models.TextField('回答内容')
    keywords = models.CharField('关键词', max_length=255, blank=True, default='')
    sort_order = models.PositiveIntegerField('排序', default=100)
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        app_label = 'tools'
        verbose_name = 'TARDIS 客服语料'
        verbose_name_plural = 'TARDIS 客服语料'
        ordering = ['sort_order', '-updated_at', '-id']

    def __str__(self):
        return self.title


class TushareRagEntry(models.Model):
    """Tushare 页面客服语料"""

    title = models.CharField('标题', max_length=120)
    question_hint = models.CharField('问题提示', max_length=255, blank=True, default='')
    answer = models.TextField('回答内容')
    keywords = models.CharField('关键词', max_length=255, blank=True, default='')
    sort_order = models.PositiveIntegerField('排序', default=100)
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        app_label = 'tools'
        verbose_name = 'Tushare 客服语料'
        verbose_name_plural = 'Tushare 客服语料'
        ordering = ['sort_order', '-updated_at', '-id']

    def __str__(self):
        return self.title


class EdgeInferenceOffer(models.Model):
    """边缘推理节点/实例供给"""

    class BillingUnit(models.TextChoices):
        HOUR = 'hour', '按小时'
        DAY = 'day', '按天'
        MONTH = 'month', '按月'

    slug = models.SlugField('标识', max_length=64, unique=True)
    name = models.CharField('名称', max_length=120)
    provider = models.CharField('提供方', max_length=80, blank=True, default='')
    gpu_name = models.CharField('GPU 型号', max_length=120)
    gpu_count = models.PositiveIntegerField('GPU 数量', default=1)
    vram_gb = models.DecimalField('显存(GB)', max_digits=6, decimal_places=1, default=0)
    cpu_cores = models.PositiveIntegerField('CPU 核数', default=0)
    ram_gb = models.PositiveIntegerField('内存(GB)', default=0)
    disk_gb = models.PositiveIntegerField('磁盘(GB)', default=0)
    region = models.CharField('区域', max_length=120, blank=True, default='')
    network_up_mbps = models.PositiveIntegerField('上行(Mbps)', default=0)
    network_down_mbps = models.PositiveIntegerField('下行(Mbps)', default=0)
    billing_unit = models.CharField('计费单位', max_length=16, choices=BillingUnit.choices, default=BillingUnit.HOUR)
    price = models.DecimalField('价格', max_digits=10, decimal_places=2, default=0)
    min_rental_hours = models.PositiveIntegerField('最短租用时长(小时)', default=1)
    stock = models.PositiveIntegerField('库存/槽位', default=1)
    supported_models = models.CharField('支持模型', max_length=255, blank=True, default='')
    endpoint_protocols = models.CharField('接入协议', max_length=255, blank=True, default='')
    relay_service = models.ForeignKey(
        ApiRelayService,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='edge_offers',
        verbose_name='关联 API 转接服务',
    )
    description = models.TextField('说明', blank=True, default='')
    is_active = models.BooleanField('是否启用', default=True)
    sort_order = models.PositiveIntegerField('排序', default=100)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        app_label = 'tools'
        verbose_name = '边缘推理供给'
        verbose_name_plural = '边缘推理供给'
        ordering = ['sort_order', 'price', 'name']

    def __str__(self):
        return self.name


class EdgeInferenceRequest(models.Model):
    """边缘推理租用/开通请求"""

    class Status(models.TextChoices):
        PENDING = 'pending', '待处理'
        APPROVED = 'approved', '已通过'
        ACTIVE = 'active', '运行中'
        COMPLETED = 'completed', '已结束'
        REJECTED = 'rejected', '已拒绝'

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='edge_inference_requests', verbose_name='用户')
    offer = models.ForeignKey(EdgeInferenceOffer, on_delete=models.SET_NULL, null=True, blank=True, related_name='requests', verbose_name='供给')
    contact_name = models.CharField('联系人', max_length=80)
    email = models.EmailField('邮箱')
    wechat = models.CharField('微信/Telegram', max_length=80, blank=True, default='')
    requested_model = models.CharField('目标模型/镜像', max_length=255, blank=True, default='')
    use_case = models.TextField('用途说明')
    expected_concurrency = models.PositiveIntegerField('预期并发', default=1)
    expected_hours = models.PositiveIntegerField('预期时长(小时)', default=1)
    budget = models.DecimalField('预算', max_digits=10, decimal_places=2, default=0)
    status = models.CharField('状态', max_length=24, choices=Status.choices, default=Status.PENDING)
    public_endpoint = models.CharField('推理入口', max_length=255, blank=True, default='')
    api_key_prefix = models.CharField('访问 Key 前缀', max_length=32, unique=True, blank=True, null=True, default=None)
    api_key_secret_hash = models.CharField('访问 Key 哈希', max_length=255, blank=True, default='')
    api_key_last4 = models.CharField('访问 Key 后四位', max_length=4, blank=True, default='')
    api_key_created_at = models.DateTimeField('访问 Key 生成时间', blank=True, null=True)
    ssh_host = models.CharField('SSH Host', max_length=120, blank=True, default='')
    ssh_port = models.PositiveIntegerField('SSH Port', default=22)
    ssh_username = models.CharField('SSH 用户名', max_length=64, blank=True, default='')
    access_note = models.TextField('访问说明', blank=True, default='')
    activated_at = models.DateTimeField('开通时间', blank=True, null=True)
    expires_at = models.DateTimeField('到期时间', blank=True, null=True)
    admin_note = models.TextField('后台备注', blank=True, default='')
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        app_label = 'tools'
        verbose_name = '边缘推理请求'
        verbose_name_plural = '边缘推理请求'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.contact_name} - {self.requested_model or self.offer_id or "edge"}'

    @property
    def has_access_key(self) -> bool:
        return bool(self.api_key_prefix and self.api_key_secret_hash)

    def issue_access_key(self) -> str:
        prefix = f'eik_{secrets.token_hex(4)}'
        secret = secrets.token_urlsafe(24)
        self.api_key_prefix = prefix
        self.api_key_secret_hash = make_password(secret)
        self.api_key_last4 = secret[-4:]
        self.api_key_created_at = timezone.now()
        return f'{prefix}.{secret}'

    def apply_relay_access(self, access: UserApiRelayAccess):
        self.public_endpoint = f'https://ai-tool.indevs.in{access.service.public_url_path}'
        self.api_key_prefix = access.api_key_prefix
        self.api_key_secret_hash = access.api_key_secret_hash
        self.api_key_last4 = access.api_key_last4
        self.api_key_created_at = access.api_key_created_at
