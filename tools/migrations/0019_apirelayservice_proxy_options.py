from django.db import migrations, models


def seed_tushare_proxy_metadata(apps, schema_editor):
    apps.get_model('tools', 'ApiRelayService').objects.filter(slug='tushare').update(
        public_path='/tushare/',
        description='Tushare 数据中继服务。需要先注册 AI tools 账号，再由管理员在后台开通权限。',
        example_paths='/health\n/daily/news\n/daily/000002.SZ/latest',
    )


class Migration(migrations.Migration):

    dependencies = [
        ('tools', '0018_api_relay_service_and_access'),
    ]

    operations = [
        migrations.AddField(
            model_name='apirelayservice',
            name='description',
            field=models.TextField(blank=True, default='', verbose_name='服务说明'),
        ),
        migrations.AddField(
            model_name='apirelayservice',
            name='example_paths',
            field=models.TextField(blank=True, default='', verbose_name='示例路径（每行一个）'),
        ),
        migrations.AddField(
            model_name='apirelayservice',
            name='public_path',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='对外访问路径'),
        ),
        migrations.AddField(
            model_name='apirelayservice',
            name='upstream_headers',
            field=models.TextField(blank=True, default='', verbose_name='固定上游请求头(JSON)'),
        ),
        migrations.AddField(
            model_name='apirelayservice',
            name='upstream_query_params',
            field=models.TextField(blank=True, default='', verbose_name='固定上游查询参数(JSON)'),
        ),
        migrations.RunPython(seed_tushare_proxy_metadata, migrations.RunPython.noop),
    ]
