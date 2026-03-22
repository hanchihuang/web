from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('tools', '0022_tardisragentry'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='EdgeInferenceOffer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.SlugField(max_length=64, unique=True, verbose_name='标识')),
                ('name', models.CharField(max_length=120, verbose_name='名称')),
                ('provider', models.CharField(blank=True, default='', max_length=80, verbose_name='提供方')),
                ('gpu_name', models.CharField(max_length=120, verbose_name='GPU 型号')),
                ('gpu_count', models.PositiveIntegerField(default=1, verbose_name='GPU 数量')),
                ('vram_gb', models.DecimalField(decimal_places=1, default=0, max_digits=6, verbose_name='显存(GB)')),
                ('cpu_cores', models.PositiveIntegerField(default=0, verbose_name='CPU 核数')),
                ('ram_gb', models.PositiveIntegerField(default=0, verbose_name='内存(GB)')),
                ('disk_gb', models.PositiveIntegerField(default=0, verbose_name='磁盘(GB)')),
                ('region', models.CharField(blank=True, default='', max_length=120, verbose_name='区域')),
                ('network_up_mbps', models.PositiveIntegerField(default=0, verbose_name='上行(Mbps)')),
                ('network_down_mbps', models.PositiveIntegerField(default=0, verbose_name='下行(Mbps)')),
                ('billing_unit', models.CharField(choices=[('hour', '按小时'), ('day', '按天'), ('month', '按月')], default='hour', max_length=16, verbose_name='计费单位')),
                ('price', models.DecimalField(decimal_places=2, default=0, max_digits=10, verbose_name='价格')),
                ('min_rental_hours', models.PositiveIntegerField(default=1, verbose_name='最短租用时长(小时)')),
                ('stock', models.PositiveIntegerField(default=1, verbose_name='库存/槽位')),
                ('supported_models', models.CharField(blank=True, default='', max_length=255, verbose_name='支持模型')),
                ('endpoint_protocols', models.CharField(blank=True, default='', max_length=255, verbose_name='接入协议')),
                ('description', models.TextField(blank=True, default='', verbose_name='说明')),
                ('is_active', models.BooleanField(default=True, verbose_name='是否启用')),
                ('sort_order', models.PositiveIntegerField(default=100, verbose_name='排序')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
            ],
            options={
                'verbose_name': '边缘推理供给',
                'verbose_name_plural': '边缘推理供给',
                'ordering': ['sort_order', 'price', 'name'],
            },
        ),
        migrations.CreateModel(
            name='EdgeInferenceRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('contact_name', models.CharField(max_length=80, verbose_name='联系人')),
                ('email', models.EmailField(max_length=254, verbose_name='邮箱')),
                ('wechat', models.CharField(blank=True, default='', max_length=80, verbose_name='微信/Telegram')),
                ('requested_model', models.CharField(blank=True, default='', max_length=255, verbose_name='目标模型/镜像')),
                ('use_case', models.TextField(verbose_name='用途说明')),
                ('expected_concurrency', models.PositiveIntegerField(default=1, verbose_name='预期并发')),
                ('expected_hours', models.PositiveIntegerField(default=1, verbose_name='预期时长(小时)')),
                ('budget', models.DecimalField(decimal_places=2, default=0, max_digits=10, verbose_name='预算')),
                ('status', models.CharField(choices=[('pending', '待处理'), ('approved', '已通过'), ('active', '运行中'), ('completed', '已结束'), ('rejected', '已拒绝')], default='pending', max_length=24, verbose_name='状态')),
                ('admin_note', models.TextField(blank=True, default='', verbose_name='后台备注')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('offer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='requests', to='tools.edgeinferenceoffer', verbose_name='供给')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='edge_inference_requests', to=settings.AUTH_USER_MODEL, verbose_name='用户')),
            ],
            options={
                'verbose_name': '边缘推理请求',
                'verbose_name_plural': '边缘推理请求',
                'ordering': ['-created_at'],
            },
        ),
    ]
