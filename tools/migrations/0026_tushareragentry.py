from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tools', '0025_edgeinferenceoffer_relay_service'),
    ]

    operations = [
        migrations.CreateModel(
            name='TushareRagEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=120, verbose_name='标题')),
                ('question_hint', models.CharField(blank=True, default='', max_length=255, verbose_name='问题提示')),
                ('answer', models.TextField(verbose_name='回答内容')),
                ('keywords', models.CharField(blank=True, default='', max_length=255, verbose_name='关键词')),
                ('sort_order', models.PositiveIntegerField(default=100, verbose_name='排序')),
                ('is_active', models.BooleanField(default=True, verbose_name='是否启用')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
            ],
            options={
                'verbose_name': 'Tushare 客服语料',
                'verbose_name_plural': 'Tushare 客服语料',
                'ordering': ['sort_order', '-updated_at', '-id'],
            },
        ),
    ]
