from django.db import migrations, models


def seed_tushare_api_key_mode(apps, schema_editor):
    apps.get_model('tools', 'ApiRelayService').objects.filter(slug='tushare').update(
        require_api_key=True,
        require_login=False,
        description='Tushare 数据中继服务。网页登录拿权限的方式已取消，改为由超级管理员发放 API Key。',
    )


class Migration(migrations.Migration):

    dependencies = [
        ('tools', '0020_apirelayservice_apply_url'),
    ]

    operations = [
        migrations.AddField(
            model_name='apirelayservice',
            name='require_api_key',
            field=models.BooleanField(default=False, verbose_name='要求 API Key'),
        ),
        migrations.AddField(
            model_name='userapirelayaccess',
            name='api_key_created_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='API Key 生成时间'),
        ),
        migrations.AddField(
            model_name='userapirelayaccess',
            name='api_key_last4',
            field=models.CharField(blank=True, default='', max_length=4, verbose_name='API Key 后四位'),
        ),
        migrations.AddField(
            model_name='userapirelayaccess',
            name='api_key_prefix',
            field=models.CharField(blank=True, default=None, max_length=32, null=True, unique=True, verbose_name='API Key 前缀'),
        ),
        migrations.AddField(
            model_name='userapirelayaccess',
            name='api_key_secret_hash',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='API Key 哈希'),
        ),
        migrations.RunPython(seed_tushare_api_key_mode, migrations.RunPython.noop),
    ]
