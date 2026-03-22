from django.conf import settings
from django.db import migrations, models


def seed_default_tushare_service(apps, schema_editor):
    ApiRelayService = apps.get_model('tools', 'ApiRelayService')
    ApiRelayService.objects.get_or_create(
        slug='tushare',
        defaults={
            'name': 'Tushare Relay',
            'base_url': 'http://127.0.0.1:8001',
            'is_active': True,
            'require_login': True,
            'require_manual_approval': True,
            'allowed_methods': 'GET,POST',
            'timeout_seconds': 60,
            'note': '默认的 Tushare 数据转接服务',
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ('tools', '0017_ttscreditaccount_default_unlimited'),
    ]

    operations = [
        migrations.CreateModel(
            name='ApiRelayService',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.SlugField(max_length=64, unique=True, verbose_name='服务标识')),
                ('name', models.CharField(max_length=120, verbose_name='服务名称')),
                ('base_url', models.URLField(verbose_name='上游基础地址')),
                ('is_active', models.BooleanField(default=True, verbose_name='是否启用')),
                ('require_login', models.BooleanField(default=True, verbose_name='需要登录')),
                ('require_manual_approval', models.BooleanField(default=True, verbose_name='需要后台授权')),
                ('allowed_methods', models.CharField(default='GET,POST', max_length=64, verbose_name='允许方法')),
                ('timeout_seconds', models.PositiveIntegerField(default=60, verbose_name='请求超时（秒）')),
                ('note', models.CharField(blank=True, max_length=255, verbose_name='备注')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
            ],
            options={
                'verbose_name': 'API转接服务',
                'verbose_name_plural': 'API转接服务',
                'ordering': ['name', 'slug'],
            },
        ),
        migrations.CreateModel(
            name='UserApiRelayAccess',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_enabled', models.BooleanField(default=False, verbose_name='是否已授权')),
                ('approved_at', models.DateTimeField(blank=True, null=True, verbose_name='授权时间')),
                ('expires_at', models.DateTimeField(blank=True, null=True, verbose_name='过期时间')),
                ('note', models.CharField(blank=True, max_length=255, verbose_name='备注')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('service', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='user_accesses', to='tools.apirelayservice', verbose_name='服务')),
                ('user', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='api_relay_accesses', to=settings.AUTH_USER_MODEL, verbose_name='用户')),
            ],
            options={
                'verbose_name': '用户 API 访问授权',
                'verbose_name_plural': '用户 API 访问授权',
                'ordering': ['-updated_at'],
                'unique_together': {('user', 'service')},
            },
        ),
        migrations.RunPython(seed_default_tushare_service, migrations.RunPython.noop),
    ]
