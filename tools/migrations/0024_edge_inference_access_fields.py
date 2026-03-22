from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tools', '0023_edge_inference_models'),
    ]

    operations = [
        migrations.AddField(
            model_name='edgeinferencerequest',
            name='access_note',
            field=models.TextField(blank=True, default='', verbose_name='访问说明'),
        ),
        migrations.AddField(
            model_name='edgeinferencerequest',
            name='activated_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='开通时间'),
        ),
        migrations.AddField(
            model_name='edgeinferencerequest',
            name='api_key_created_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='访问 Key 生成时间'),
        ),
        migrations.AddField(
            model_name='edgeinferencerequest',
            name='api_key_last4',
            field=models.CharField(blank=True, default='', max_length=4, verbose_name='访问 Key 后四位'),
        ),
        migrations.AddField(
            model_name='edgeinferencerequest',
            name='api_key_prefix',
            field=models.CharField(blank=True, default=None, max_length=32, null=True, unique=True, verbose_name='访问 Key 前缀'),
        ),
        migrations.AddField(
            model_name='edgeinferencerequest',
            name='api_key_secret_hash',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='访问 Key 哈希'),
        ),
        migrations.AddField(
            model_name='edgeinferencerequest',
            name='expires_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='到期时间'),
        ),
        migrations.AddField(
            model_name='edgeinferencerequest',
            name='public_endpoint',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='推理入口'),
        ),
        migrations.AddField(
            model_name='edgeinferencerequest',
            name='ssh_host',
            field=models.CharField(blank=True, default='', max_length=120, verbose_name='SSH Host'),
        ),
        migrations.AddField(
            model_name='edgeinferencerequest',
            name='ssh_port',
            field=models.PositiveIntegerField(default=22, verbose_name='SSH Port'),
        ),
        migrations.AddField(
            model_name='edgeinferencerequest',
            name='ssh_username',
            field=models.CharField(blank=True, default='', max_length=64, verbose_name='SSH 用户名'),
        ),
    ]
