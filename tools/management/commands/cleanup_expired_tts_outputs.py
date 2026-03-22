from pathlib import Path

from django.core.management.base import BaseCommand
from django.utils import timezone

from tools.models import TTSOrder
from tools.tts_retention import archive_tts_file, should_archive_special_tts


class Command(BaseCommand):
    help = '清理已过期的 TTS 交付音频文件。'

    def handle(self, *args, **options):
        archived_count = 0
        archive_candidates = TTSOrder.objects.filter(
            output_file__isnull=False,
            delivered_at__isnull=False,
        ).exclude(output_file='')
        for order in archive_candidates:
            if not should_archive_special_tts(order.delivered_at):
                continue
            try:
                archive_path = archive_tts_file(order, Path(order.output_file.path))
            except Exception:
                archive_path = None
            if archive_path:
                archived_count += 1

        expired_orders = TTSOrder.objects.filter(
            output_file__isnull=False,
            output_expires_at__isnull=False,
            output_expires_at__lte=timezone.now(),
        ).exclude(output_file='')

        cleaned_count = 0
        for order in expired_orders:
            file_name = order.output_file.name
            archive_note = ''
            try:
                archive_path = archive_tts_file(order, Path(order.output_file.path))
                if archive_path:
                    archive_note = f'；已备份到 {archive_path}'
            except Exception:
                archive_note = ''
            order.output_file.delete(save=False)
            timestamp = timezone.now().strftime('%F %T')
            log_parts = [part for part in [order.processing_log.strip(), f'{timestamp} 交付文件已过期并清理: {file_name}{archive_note}'] if part]
            order.output_file = ''
            order.output_duration_seconds = None
            order.processing_log = '\n'.join(log_parts)
            order.save(update_fields=['output_file', 'output_duration_seconds', 'processing_log', 'updated_at'])
            cleaned_count += 1

        self.stdout.write(self.style.SUCCESS(f'已备份 {archived_count} 个特殊时间点音频，已清理 {cleaned_count} 个过期音频文件。'))
