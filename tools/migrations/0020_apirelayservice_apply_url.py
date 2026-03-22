from django.db import migrations, models


def seed_tushare_apply_url(apps, schema_editor):
    apps.get_model('tools', 'ApiRelayService').objects.filter(slug='tushare').update(
        apply_url='/quant/tushare-pro-guide/',
    )


class Migration(migrations.Migration):

    dependencies = [
        ('tools', '0019_apirelayservice_proxy_options'),
    ]

    operations = [
        migrations.AddField(
            model_name='apirelayservice',
            name='apply_url',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='申请/说明页'),
        ),
        migrations.RunPython(seed_tushare_apply_url, migrations.RunPython.noop),
    ]
