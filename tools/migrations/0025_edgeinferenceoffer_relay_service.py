from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tools', '0024_edge_inference_access_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='edgeinferenceoffer',
            name='relay_service',
            field=models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name='edge_offers', to='tools.apirelayservice', verbose_name='关联 API 转接服务'),
        ),
    ]
