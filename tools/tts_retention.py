import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from django.utils import timezone


SPECIAL_ARCHIVE_CUTOFF = datetime(2026, 3, 22, 5, 0, 0, tzinfo=ZoneInfo('Asia/Shanghai'))
SPECIAL_ARCHIVE_DIR = Path.home() / '图片' / 'tts_saved_before_2026-03-22_0500'


def should_archive_special_tts(delivered_at) -> bool:
    if not delivered_at:
        return False
    local_delivered = timezone.localtime(delivered_at, ZoneInfo('Asia/Shanghai'))
    return local_delivered < SPECIAL_ARCHIVE_CUTOFF


def build_archive_path(order, source_path: Path) -> Path:
    suffix = source_path.suffix or '.mp3'
    return SPECIAL_ARCHIVE_DIR / f'{order.order_no}{suffix}'


def archive_tts_file(order, source_path: Path) -> Path | None:
    if not should_archive_special_tts(order.delivered_at):
        return None
    if not source_path.exists():
        return None
    SPECIAL_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = build_archive_path(order, source_path)
    if archive_path.exists():
        return archive_path
    shutil.copy2(source_path, archive_path)
    return archive_path
