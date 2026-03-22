from django import forms
from django.contrib.auth.models import User

from .models import TTSOrder, TTSCreditRechargeOrder, EdgeInferenceRequest
from .tts import DEFAULT_RECHARGE_PACKS, VOICE_PRESET_CONFIG


class TTSOrderForm(forms.ModelForm):
    class Meta:
        model = TTSOrder
        fields = [
            'contact_name',
            'email',
            'wechat',
            'company',
            'voice_preset',
            'style_notes',
            'business_usage',
            'delivery_format',
            'source_text',
        ]
        widgets = {
            'contact_name': forms.TextInput(attrs={'placeholder': '怎么称呼你'}),
            'email': forms.EmailInput(attrs={'placeholder': '用于接收交付文件'}),
            'wechat': forms.TextInput(attrs={'placeholder': '微信 / Telegram / WhatsApp'}),
            'company': forms.TextInput(attrs={'placeholder': '品牌名，可选'}),
            'style_notes': forms.TextInput(attrs={'placeholder': '例如：更亲切、适合知识付费、节奏稳一点'}),
            'source_text': forms.Textarea(attrs={'rows': 12, 'placeholder': '粘贴你要转语音的中文文本'}),
        }

    def clean_source_text(self):
        text = (self.cleaned_data.get('source_text') or '').strip()
        if len(text) < 20:
            raise forms.ValidationError('文本太短，至少 20 个字。')
        if len(text) > 60000:
            raise forms.ValidationError('单次下单上限为 60000 字，请拆单。')
        return text


class TTSOrderLookupForm(forms.Form):
    order_no = forms.CharField(max_length=32, label='订单号')
    email = forms.EmailField(label='邮箱')


class TTSPaymentProofForm(forms.ModelForm):
    class Meta:
        model = TTSOrder
        fields = ['payment_proof']
        widgets = {
            'payment_proof': forms.ClearableFileInput(attrs={'accept': 'image/*'}),
        }


class TTSCreditLoginForm(forms.Form):
    username = forms.CharField(max_length=150, label='用户名')
    password = forms.CharField(widget=forms.PasswordInput, label='密码')


class TTSCreditRegisterForm(forms.Form):
    username = forms.CharField(max_length=150, label='用户名')
    email = forms.EmailField(label='邮箱')
    password = forms.CharField(widget=forms.PasswordInput, label='密码')
    password_confirm = forms.CharField(widget=forms.PasswordInput, label='确认密码')

    def clean_username(self):
        username = (self.cleaned_data.get('username') or '').strip()
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError('用户名已存在。')
        return username

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError('邮箱已被注册。')
        return email

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('password') != cleaned_data.get('password_confirm'):
            raise forms.ValidationError('两次输入的密码不一致。')
        return cleaned_data


class TTSRechargeForm(forms.Form):
    char_amount = forms.TypedChoiceField(
        choices=[(item['chars'], f"{item['label']} / {item['price']} 元") for item in DEFAULT_RECHARGE_PACKS],
        coerce=int,
        label='充值套餐',
    )


class TTSCreditConsumeForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['voice_preset'].choices = [
            (key, value['display_name'])
            for key, value in VOICE_PRESET_CONFIG.items()
            if value.get('selectable', True)
        ]

    class Meta:
        model = TTSOrder
        fields = [
            'voice_preset',
            'style_notes',
            'delivery_format',
            'source_text',
        ]
        widgets = {
            'style_notes': forms.TextInput(attrs={'placeholder': '例如：更亲切、语速稳一点'}),
            'source_text': forms.Textarea(attrs={'rows': 12, 'placeholder': '粘贴你要转语音的中文文本'}),
        }

    def clean_source_text(self):
        text = (self.cleaned_data.get('source_text') or '').strip()
        if len(text) < 20:
            raise forms.ValidationError('文本太短，至少 20 个字。')
        if len(text) > 60000:
            raise forms.ValidationError('单次提交上限为 60000 字，请拆单。')
        return text


class TTSCreditRechargeProofForm(forms.ModelForm):
    class Meta:
        model = TTSCreditRechargeOrder
        fields = ['payment_proof']
        widgets = {
            'payment_proof': forms.ClearableFileInput(attrs={'accept': 'image/*'}),
        }


class EdgeInferenceRequestForm(forms.ModelForm):
    class Meta:
        model = EdgeInferenceRequest
        fields = [
            'contact_name',
            'email',
            'wechat',
            'requested_model',
            'use_case',
            'expected_concurrency',
            'expected_hours',
            'budget',
        ]
        widgets = {
            'contact_name': forms.TextInput(attrs={'placeholder': '怎么称呼你'}),
            'email': forms.EmailInput(attrs={'placeholder': '用于接收开通信息'}),
            'wechat': forms.TextInput(attrs={'placeholder': '微信 / Telegram / WhatsApp'}),
            'requested_model': forms.TextInput(attrs={'placeholder': '例如：Qwen3-32B / vLLM / SGLang / 自定义镜像'}),
            'use_case': forms.Textarea(attrs={'rows': 6, 'placeholder': '说明你的用途：推理模型、目标吞吐、是否要 API、是否要公网入口等'}),
        }

    def clean_expected_concurrency(self):
        value = int(self.cleaned_data.get('expected_concurrency') or 1)
        return max(1, value)

    def clean_expected_hours(self):
        value = int(self.cleaned_data.get('expected_hours') or 1)
        return max(1, value)

    def clean_use_case(self):
        text = (self.cleaned_data.get('use_case') or '').strip()
        if len(text) < 10:
            raise forms.ValidationError('用途说明太短，至少写 10 个字。')
        return text
