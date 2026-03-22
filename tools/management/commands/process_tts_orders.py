import subprocess
import tempfile
from pathlib import Path
import os
import shutil
from datetime import timedelta
import time
import traceback

import numpy as np
import torch
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from tools.models import TTSOrder, TTSCreditAccount, TTSCreditLedger
from tools.tts_config import get_tts_runtime_rules
from tools.qwen_runtime import DEFAULT_MAX_NEW_TOKENS
from tools.qwen_runtime import CancelRequestedError, QwenTTSRuntime
from tools.qwen_runtime import StreamingAudioWriter, estimate_max_new_tokens, plan_batches
from tools.tts_retention import archive_tts_file
from tools.tts import get_voice_preset_config


class Command(BaseCommand):
    help = '处理已付款的 TTS 订单，并调用本机 Qwen TTS 生成音频。'

    def add_arguments(self, parser):
        parser.add_argument('--order-no', help='只处理指定订单号')
        parser.add_argument('--watch', action='store_true', help='常驻运行，持续监听新订单')

    def _set_progress(self, order, percent, message, *, status=None):
        if status is not None:
            order.status = status
        order.processing_log = f'{timezone.now():%F %T} [进度 {percent}%] {message}'
        update_fields = ['processing_log', 'updated_at']
        if status is not None:
            update_fields.append('status')
        order.save(update_fields=update_fields)
        self.stdout.write(f'{order.order_no} | {percent}% | {message}')

    def _update_phase_progress(self, order, phase, **payload):
        if phase == 'text_ready':
            chars = payload.get('chars', order.char_count)
            chunks = payload.get('chunks', 1)
            batches = payload.get('batches', 1)
            chunk_size = payload.get('chunk_size', chars)
            batch_chars = payload.get('batch_chars', chars)
            direct_max_chars = payload.get('direct_max_chars', get_tts_runtime_rules()['direct_max_chars'])
            if chunks > 1:
                self._set_progress(order, 12, f'文本共 {chars} 字，超过 {direct_max_chars} 字阈值后已按每 {chunk_size} 字切成 {chunks} 段，并按 {batch_chars} 字预算组为 {batches} 个批次')
            else:
                self._set_progress(order, 12, f'文本共 {chars} 字，未超过 {direct_max_chars} 字整段直出阈值，正在检查本地模型缓存')
            return True
        if phase == 'locating_model_cache':
            self._set_progress(order, 14, '正在定位本地模型缓存')
            return True
        if phase == 'loading_model':
            device = payload.get('device', 'cuda:0')
            attn = payload.get('attn', 'default')
            compile_mode = payload.get('compile', 'off')
            self._set_progress(order, 22, f'正在加载模型到 {device}，attention={attn}，compile={compile_mode}')
            return True
        if phase == 'model_loaded':
            self._set_progress(order, 28, '模型已加载，准备进入极速生成流水线')
            return True
        if phase == 'warmup':
            self._set_progress(order, 30, f'正在执行模型预热，预热文本 {payload.get("chars", 0)} 字')
            return True
        if phase == 'batch_start':
            batch_index = payload.get('batch_index', 1)
            total_batches = payload.get('total_batches', 1)
            batch_chars = payload.get('batch_chars', 0)
            if total_batches > 1:
                self._set_progress(order, 34, f'正在生成第 {batch_index}/{total_batches} 个批次，本批 {batch_chars} 字')
            else:
                self._set_progress(order, 34, f'正在生成单批音频，本批 {batch_chars} 字')
            return True
        if phase == 'batch_done':
            batch_index = payload.get('batch_index', 1)
            total_batches = payload.get('total_batches', 1)
            gen_sec = payload.get('gen_sec', 0.0)
            audio_sec = payload.get('audio_sec', 0.0)
            percent = 34 + int(batch_index / max(total_batches, 1) * 56)
            self._set_progress(order, percent, f'第 {batch_index}/{total_batches} 个批次完成，生成耗时 {gen_sec:.2f}s，音频时长 {audio_sec:.2f}s')
            return True
        if phase == 'finalizing_audio':
            self._set_progress(order, 94, f'所有批次已完成，正在封装最终 {order.delivery_format.upper()} 文件')
            return True
        if phase == 'completed':
            elapsed_sec = payload.get('elapsed_sec', 0.0)
            audio_sec = payload.get('audio_sec', 0.0)
            self._set_progress(order, 97, f'音频已写出，总耗时 {elapsed_sec:.2f}s，成品时长 {audio_sec:.2f}s')
            return True
        return False

    def _send_delivery_email(self, order):
        public_base_url = os.getenv('TTS_PUBLIC_BASE_URL', 'https://ai-tool.indevs.in').rstrip('/')
        query_url = f'{public_base_url}/tts-studio/query/?order_no={order.order_no}&email={order.email}'
        download_url = f'{public_base_url}{order.output_file.url}'
        expires_at_text = timezone.localtime(order.output_expires_at).strftime('%Y-%m-%d %H:%M:%S') if order.output_expires_at else '3 小时后'
        subject = f'TTS 订单已交付：{order.order_no}'
        message = (
            f'你的 TTS 订单已处理完成。\n\n'
            f'订单号：{order.order_no}\n'
            f'下载地址：{download_url}\n'
            f'订单查询：{query_url}\n'
            f'请及时下载：音频将在 {expires_at_text} 自动清除，过期后链接失效。\n'
        )
        recipient = order.email or (order.user.email if order.user else '')
        if not recipient:
            raise ValueError('订单没有可用的接收邮箱')
        send_mail(subject, message, None, [recipient], fail_silently=False)

    def _refund_credits_if_needed(self, order):
        if not order.user_id or not order.payment_reference.startswith('CREDIT-'):
            return
        if order.ledger_entries.filter(entry_type=TTSCreditLedger.EntryType.REFUND).exists():
            return
        with transaction.atomic():
            account = TTSCreditAccount.objects.select_for_update().get(user=order.user)
            if not account.is_unlimited:
                account.char_balance += order.char_count
            account.total_used_chars = max(account.total_used_chars - order.char_count, 0)
            account.save(update_fields=['char_balance', 'total_used_chars', 'updated_at'])
            TTSCreditLedger.objects.create(
                user=order.user,
                entry_type=TTSCreditLedger.EntryType.REFUND,
                char_delta=order.char_count,
                balance_after=account.char_balance,
                tts_order=order,
                note=f'取消 TTS 订单 {order.order_no}，退回 {order.char_count} 字',
            )

    def _mark_order_cancelled(self, order, reason):
        order.refresh_from_db(fields=['status', 'cancel_requested', 'output_file', 'updated_at'])
        if order.status == TTSOrder.Status.CANCELLED:
            return
        self._refund_credits_if_needed(order)
        order.status = TTSOrder.Status.CANCELLED
        order.cancel_requested = False
        order.processing_log = f'{timezone.now():%F %T} [进度 0%] {reason}'
        order.save(update_fields=['status', 'cancel_requested', 'processing_log', 'updated_at'])

    def _should_cancel(self, order):
        order.refresh_from_db(fields=['cancel_requested', 'status'])
        return order.cancel_requested or order.status == TTSOrder.Status.CANCELLED

    def _recover_interrupted_orders(self):
        interrupted_orders = list(
            TTSOrder.objects.filter(
                payment_status=TTSOrder.PaymentStatus.PAID,
                status=TTSOrder.Status.GENERATING,
            ).order_by('created_at')
        )
        for order in interrupted_orders:
            if order.cancel_requested:
                self._mark_order_cancelled(order, 'worker 重启时检测到取消请求，额度已退回')
                continue
            order.status = TTSOrder.Status.QUEUED
            order.processing_log = f'{timezone.now():%F %T} [进度 5%] worker 重启后自动恢复，任务已重新入队'
            order.save(update_fields=['status', 'processing_log', 'updated_at'])
            self.stdout.write(self.style.WARNING(f'检测到中断订单，已重新入队: {order.order_no}'))

    def _build_runtime(self):
        qwen_dir = Path(getattr(settings, 'BASE_DIR')) / '..' / '图片' / 'Qwen3-TTS'
        qwen_dir = qwen_dir.resolve()
        ffmpeg_bin = shutil.which('ffmpeg') or '/home/user/anaconda3/bin/ffmpeg'
        ffprobe_bin = shutil.which('ffprobe') or '/home/user/anaconda3/bin/ffprobe'
        attn_impl = os.getenv('QWEN_TTS_ATTN_IMPLEMENTATION', 'auto').strip() or 'auto'
        if not Path(ffmpeg_bin).exists():
            raise CommandError(f'未找到 ffmpeg: {ffmpeg_bin}')
        if not Path(ffprobe_bin).exists():
            raise CommandError(f'未找到 ffprobe: {ffprobe_bin}')
        runtime = QwenTTSRuntime(
            model_size='0.6B',
            device=os.getenv('QWEN_TTS_DEVICE', 'cuda:0'),
            dtype_name=os.getenv('QWEN_TTS_DTYPE', 'bfloat16'),
            attn_implementation=attn_impl,
            compile_mode=os.getenv('QWEN_TTS_COMPILE', 'off'),
            mp3_bitrate=os.getenv('QWEN_TTS_MP3_BITRATE', '128k'),
            batch_size=int(os.getenv('QWEN_TTS_BATCH_SIZE', '8')),
            max_batch_chars=int(os.getenv('QWEN_TTS_MAX_BATCH_CHARS', '800')),
            max_chars=int(os.getenv('QWEN_TTS_MAX_CHARS', '400')),
            max_new_tokens=int(os.getenv('QWEN_TTS_MAX_NEW_TOKENS', str(DEFAULT_MAX_NEW_TOKENS))),
            pause_ms=int(os.getenv('QWEN_TTS_PAUSE_MS', '350')),
            warmup=os.getenv('QWEN_TTS_WARMUP', '0').strip().lower() not in {'0', 'false', 'off', 'no'},
            ffmpeg_bin=ffmpeg_bin,
        )
        return runtime, ffmpeg_bin, ffprobe_bin

    def _read_positive_int_env(self, name, default):
        raw = os.getenv(name, '').strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            return default
        return max(1, value)

    def _finalize_delivered_order(self, order, temp_output_path, output_path, ffprobe_bin):
        temp_output_path.replace(output_path)
        self._set_progress(order, 99, '正在整理交付文件')
        duration_result = subprocess.run(
            [
                ffprobe_bin, '-v', 'error', '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1', str(output_path)
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        duration_seconds = int(float(duration_result.stdout.strip() or '0'))

        relative_path = output_path.relative_to(settings.MEDIA_ROOT)
        order.output_file.name = str(relative_path)
        order.output_duration_seconds = duration_seconds
        order.status = TTSOrder.Status.DELIVERED
        order.delivered_at = timezone.now()
        order.output_expires_at = timezone.now() + timedelta(hours=3)
        order.processing_log = f'{timezone.now():%F %T} [进度 100%] 生成完成，文件将于 {timezone.localtime(order.output_expires_at):%F %T} 过期清理'
        order.save()
        archive_path = archive_tts_file(order, output_path)
        if archive_path:
            order.processing_log = f'{order.processing_log}\n{timezone.now():%F %T} 已按特殊保留规则备份到 {archive_path}'
            order.save(update_fields=['processing_log', 'updated_at'])
        try:
            self._send_delivery_email(order)
        except Exception as exc:
            order.processing_log = f'{timezone.now():%F %T} 生成完成，但邮件发送失败: {exc}'
            order.save(update_fields=['processing_log', 'updated_at'])
            self.stdout.write(self.style.WARNING(f'订单 {order.order_no} 音频已生成，但邮件发送失败: {exc}'))
        self.stdout.write(self.style.SUCCESS(f'已交付 {order.order_no}: {relative_path}'))

    def _mark_order_failed(self, order, exc):
        order.status = TTSOrder.Status.QUEUED
        order.processing_log = f'{timezone.now():%F %T} 生成失败: {exc}'
        order.save(update_fields=['status', 'processing_log', 'updated_at'])

    def _claim_orders(self, *, limit, order_no=''):
        with transaction.atomic():
            queryset = TTSOrder.objects.select_for_update(skip_locked=True).filter(
                payment_status=TTSOrder.PaymentStatus.PAID,
                status=TTSOrder.Status.QUEUED,
            ).order_by('created_at')
            if order_no:
                queryset = queryset.filter(order_no=order_no)
            orders = list(queryset[:limit])
            for order in orders:
                order.status = TTSOrder.Status.GENERATING
                order.processing_log = f'{timezone.now():%F %T} [进度 10%] 常驻模型 worker 已接单，准备生成'
                order.save(update_fields=['status', 'processing_log', 'updated_at'])
            return orders

    def _build_group_job(self, order, runtime, media_dir):
        order.refresh_from_db(fields=['status', 'cancel_requested'])
        if order.status == TTSOrder.Status.CANCELLED or order.cancel_requested:
            self._mark_order_cancelled(order, '用户已取消任务，额度已退回')
            return None

        preset = get_voice_preset_config(order.voice_preset, order.style_notes)
        normalized_text, chunks, direct_max_chars = runtime.prepare_text(order.source_text)
        if not chunks:
            self._mark_order_failed(order, ValueError('待转文本为空'))
            return None
        planned_batches = plan_batches(chunks, runtime.batch_size, runtime.max_batch_chars)
        self._update_phase_progress(
            order,
            'text_ready',
            chars=len(normalized_text),
            chunks=len(chunks),
            batches=max(1, len(planned_batches)),
            direct_max_chars=direct_max_chars,
            chunk_size=runtime.max_chars,
            batch_chars=runtime.max_batch_chars,
        )

        temp_dir = tempfile.TemporaryDirectory()
        temp_dir_path = Path(temp_dir.name)
        output_path = media_dir / f'{order.order_no}.{order.delivery_format}'
        temp_output_path = temp_dir_path / output_path.name
        return {
            'order': order,
            'preset': preset,
            'chunks': chunks,
            'next_chunk_index': 0,
            'total_chunks': len(chunks),
            'total_audio_samples': 0,
            'writer': None,
            'sr': 0,
            'pause': None,
            'temp_dir': temp_dir,
            'temp_output_path': temp_output_path,
            'output_path': output_path,
            'started_at': time.perf_counter(),
        }

    def _process_order_group(self, orders, runtime, ffmpeg_bin, ffprobe_bin, *, group_limit, order_no=''):
        if not orders:
            return

        media_dir = Path(settings.MEDIA_ROOT) / 'tts_orders'
        media_dir.mkdir(parents=True, exist_ok=True)
        jobs = []

        for order in orders:
            job = self._build_group_job(order, runtime, media_dir)
            if job is not None:
                jobs.append(job)

        if not jobs:
            return

        progress_callback = lambda phase, **payload: [self._update_phase_progress(job['order'], phase, **payload) for job in jobs]
        runtime.load(progress_callback=progress_callback)
        runtime._maybe_warmup(
            tts=runtime.tts,
            chunks=[job['chunks'][0] for job in jobs if job['chunks']],
            language='Chinese',
            speaker=jobs[0]['preset']['speaker'],
            instruct=jobs[0]['preset']['instruction'],
            progress_callback=progress_callback,
        )

        while jobs:
            if not order_no and len(jobs) < group_limit:
                refill_orders = self._claim_orders(limit=group_limit - len(jobs))
                for refill_order in refill_orders:
                    refill_job = self._build_group_job(refill_order, runtime, media_dir)
                    if refill_job is not None:
                        jobs.append(refill_job)

            remaining_jobs = []
            batch_items = []
            batch_jobs = []
            batch_chars = 0

            for job in jobs:
                order = job['order']
                if self._should_cancel(order):
                    if job['writer'] is not None:
                        job['writer'].close()
                    job['temp_dir'].cleanup()
                    self._mark_order_cancelled(order, '用户已取消任务，额度已退回')
                    continue
                if job['next_chunk_index'] >= job['total_chunks']:
                    remaining_jobs.append(job)
                    continue

                chunk = job['chunks'][job['next_chunk_index']]
                if batch_items and (len(batch_items) >= runtime.batch_size or batch_chars + len(chunk) > runtime.max_batch_chars):
                    remaining_jobs.append(job)
                    continue

                batch_items.append({
                    'text': chunk,
                    'language': 'Chinese',
                    'speaker': job['preset']['speaker'],
                    'instruct': job['preset']['instruction'],
                    'max_new_tokens': estimate_max_new_tokens(chunk, runtime.max_new_tokens),
                })
                batch_jobs.append(job)
                batch_chars += len(chunk)
                remaining_jobs.append(job)

            if not batch_items:
                break

            for job in batch_jobs:
                order = job['order']
                self._set_progress(
                    order,
                    34,
                    f'正在并行生成第 {job["next_chunk_index"] + 1}/{max(job["total_chunks"], 1)} 段，本段 {len(job["chunks"][job["next_chunk_index"]])} 字',
                )

            if torch.cuda.is_available():
                torch.cuda.synchronize()
            batch_started = time.perf_counter()
            with torch.inference_mode():
                results, current_sr = runtime.generate_batch_items(
                    items=batch_items,
                    progress_callback=lambda phase, **payload: [self._update_phase_progress(job['order'], phase, **payload) for job in batch_jobs],
                )
            if torch.cuda.is_available():
                torch.cuda.synchronize()
            batch_elapsed = time.perf_counter() - batch_started

            survivors = []
            for job, result in zip(batch_jobs, results):
                order = job['order']
                if isinstance(result, Exception):
                    if job['writer'] is not None:
                        job['writer'].close()
                    job['temp_dir'].cleanup()
                    self._mark_order_failed(order, result)
                    self.stderr.write(f'{order.order_no} grouped batch item failed: {result}')
                    continue

                try:
                    if job['writer'] is None:
                        job['sr'] = current_sr
                        job['writer'] = StreamingAudioWriter(
                            job['temp_output_path'],
                            audio_format=order.delivery_format,
                            sr=current_sr,
                            mp3_bitrate=runtime.mp3_bitrate,
                            ffmpeg_bin=runtime.ffmpeg_bin,
                        )
                        if runtime.pause_ms:
                            job['pause'] = np.zeros(int(current_sr * runtime.pause_ms / 1000), dtype=np.float32)
                        else:
                            job['pause'] = None

                    wav = np.asarray(result, dtype=np.float32)
                    job['writer'].write(wav)
                    job['total_audio_samples'] += len(wav)
                    job['next_chunk_index'] += 1

                    if job['pause'] is not None and job['next_chunk_index'] < job['total_chunks']:
                        job['writer'].write(job['pause'])
                        job['total_audio_samples'] += len(job['pause'])

                    audio_sec = (len(wav) / job['sr']) if job['sr'] else 0
                    percent = 34 + int(job['next_chunk_index'] / max(job['total_chunks'], 1) * 56)
                    self._set_progress(
                        order,
                        percent,
                        f'第 {job["next_chunk_index"]}/{max(job["total_chunks"], 1)} 段完成，最近一段耗时 {batch_elapsed:.2f}s，音频时长 {audio_sec:.2f}s',
                    )

                    if job['next_chunk_index'] >= job['total_chunks']:
                        self._set_progress(order, 94, f'所有片段已完成，正在封装最终 {order.delivery_format.upper()} 文件')
                        job['writer'].close()
                        self._finalize_delivered_order(order, job['temp_output_path'], job['output_path'], ffprobe_bin)
                        job['temp_dir'].cleanup()
                    else:
                        survivors.append(job)
                except Exception as exc:
                    if job['writer'] is not None:
                        try:
                            job['writer'].close()
                        except Exception:
                            pass
                    job['temp_dir'].cleanup()
                    self._mark_order_failed(order, exc)
                    self.stderr.write(traceback.format_exc())

            jobs = [job for job in remaining_jobs if job not in batch_jobs] + survivors

    def _process_order(self, order, runtime, ffmpeg_bin, ffprobe_bin):
        order.refresh_from_db(fields=['status', 'cancel_requested'])
        if order.status == TTSOrder.Status.CANCELLED or order.cancel_requested:
            self._mark_order_cancelled(order, '用户已取消任务，额度已退回')
            return

        media_dir = Path(settings.MEDIA_ROOT) / 'tts_orders'
        media_dir.mkdir(parents=True, exist_ok=True)

        preset = get_voice_preset_config(order.voice_preset, order.style_notes)
        self.stdout.write(f'开始处理 {order.order_no} ...')
        self._set_progress(order, 10, '常驻模型 worker 已接单，准备生成', status=TTSOrder.Status.GENERATING)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)
            output_path = media_dir / f'{order.order_no}.{order.delivery_format}'
            temp_output_path = temp_dir_path / output_path.name
            try:
                _, duration_seconds = runtime.synthesize_to_file(
                    text=order.source_text,
                    output_path=temp_output_path,
                    speaker=preset['speaker'],
                    language='Chinese',
                    instruct=preset['instruction'],
                    output_format=order.delivery_format,
                    progress_callback=lambda phase, **payload: self._update_phase_progress(order, phase, **payload),
                    should_cancel=lambda: self._should_cancel(order),
                )

                temp_output_path.replace(output_path)

                self._set_progress(order, 99, '正在整理交付文件')
                duration_result = subprocess.run(
                    [
                        ffprobe_bin, '-v', 'error', '-show_entries', 'format=duration',
                        '-of', 'default=noprint_wrappers=1:nokey=1', str(output_path)
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                duration_seconds = int(float(duration_result.stdout.strip() or duration_seconds or '0'))
            except CancelRequestedError:
                self._mark_order_cancelled(order, '用户已取消任务，额度已退回')
                self.stdout.write(self.style.WARNING(f'订单 {order.order_no} 已取消'))
                return
            except (subprocess.CalledProcessError, FileNotFoundError, Exception) as exc:
                order.status = TTSOrder.Status.QUEUED
                order.processing_log = f'{timezone.now():%F %T} 生成失败: {exc}'
                order.save(update_fields=['status', 'processing_log', 'updated_at'])
                self.stderr.write(traceback.format_exc())
                raise CommandError(f'订单 {order.order_no} 处理失败: {exc}') from exc

            relative_path = output_path.relative_to(settings.MEDIA_ROOT)
            order.output_file.name = str(relative_path)
            order.output_duration_seconds = duration_seconds
            order.status = TTSOrder.Status.DELIVERED
            order.delivered_at = timezone.now()
            order.output_expires_at = timezone.now() + timedelta(hours=3)
            order.processing_log = f'{timezone.now():%F %T} [进度 100%] 生成完成，文件将于 {timezone.localtime(order.output_expires_at):%F %T} 过期清理'
            order.save()
            archive_path = archive_tts_file(order, output_path)
            if archive_path:
                order.processing_log = f'{order.processing_log}\n{timezone.now():%F %T} 已按特殊保留规则备份到 {archive_path}'
                order.save(update_fields=['processing_log', 'updated_at'])
            try:
                self._send_delivery_email(order)
            except Exception as exc:
                order.processing_log = f'{timezone.now():%F %T} 生成完成，但邮件发送失败: {exc}'
                order.save(update_fields=['processing_log', 'updated_at'])
                self.stdout.write(self.style.WARNING(f'订单 {order.order_no} 音频已生成，但邮件发送失败: {exc}'))
            self.stdout.write(self.style.SUCCESS(f'已交付 {order.order_no}: {relative_path}'))

    def handle(self, *args, **options):
        self.stdout.write('先清理已过期交付文件...')
        from django.core.management import call_command
        call_command('cleanup_expired_tts_outputs')
        self._recover_interrupted_orders()

        runtime, ffmpeg_bin, ffprobe_bin = self._build_runtime()
        watch = options['watch']
        idle_rounds = 0
        group_limit = self._read_positive_int_env('QWEN_TTS_MAX_CONCURRENT_ORDERS', max(1, runtime.batch_size))

        while True:
            orders = self._claim_orders(limit=group_limit, order_no=options['order_no'] or '')

            if not orders:
                if not watch:
                    self.stdout.write(self.style.WARNING('没有待处理的已付款 TTS 订单。'))
                    return
                idle_rounds += 1
                if idle_rounds == 1:
                    self.stdout.write('常驻 TTS worker 空闲中，等待新订单...')
                time.sleep(2)
                continue

            idle_rounds = 0
            self._process_order_group(
                orders,
                runtime,
                ffmpeg_bin,
                ffprobe_bin,
                group_limit=group_limit,
                order_no=options['order_no'] or '',
            )

            if not watch and options['order_no']:
                return
