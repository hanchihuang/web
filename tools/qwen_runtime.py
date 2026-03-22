import gc
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from huggingface_hub import snapshot_download

from .tts_config import get_tts_runtime_rules


QWEN_DIR = Path(os.getenv('QWEN_TTS_SOURCE_DIR', '/home/user/图片/Qwen3-TTS')).expanduser()
if str(QWEN_DIR) not in sys.path:
    sys.path.insert(0, str(QWEN_DIR))

from qwen_tts import Qwen3TTSModel  # noqa: E402


class CancelRequestedError(RuntimeError):
    pass


# Keep the default conservative so a single batch cannot run for tens of
# minutes. Longer texts should be split into smaller batches instead.
DEFAULT_MAX_NEW_TOKENS = 1536


SYMBOL_REPLACEMENTS = [
    ('≤', '小于等于'),
    ('≥', '大于等于'),
    ('≠', '不等于'),
    ('≈', '约等于'),
    ('≡', '恒等于'),
    ('±', '正负'),
    ('×', '乘以'),
    ('÷', '除以'),
    ('∞', '无穷大'),
    ('∑', '求和'),
    ('∏', '连乘'),
    ('√', '根号'),
    ('∫', '积分'),
    ('∂', '偏导'),
    ('∆', '增量'),
    ('δ', 'delta'),
    ('α', 'alpha'),
    ('β', 'beta'),
    ('γ', 'gamma'),
    ('λ', 'lambda'),
    ('μ', 'mu'),
    ('π', 'pi'),
    ('σ', 'sigma'),
    ('θ', 'theta'),
    ('ω', 'omega'),
    ('°', '度'),
    ('℃', '摄氏度'),
    ('℉', '华氏度'),
    ('%','百分之'),
    ('&', '和'),
    ('@', '艾特'),
]


FORMULA_PLACEHOLDER = '这里有一段公式内容。'
CODE_PLACEHOLDER = '这里有一段代码内容。'
TABLE_PLACEHOLDER = '这里有一段表格内容。'


def _looks_formula_heavy(fragment: str) -> bool:
    sample = (fragment or '').strip()
    if not sample:
        return False
    if len(sample) <= 3:
        return False

    math_like = len(re.findall(r'[=+\-*/^_<>≈≠≤≥∑∏√∫%()]', sample))
    digit_count = len(re.findall(r'\d', sample))
    alpha_count = len(re.findall(r'[A-Za-z]', sample))
    chinese_count = len(re.findall(r'[\u4e00-\u9fff]', sample))
    symbol_density = math_like / max(len(sample), 1)

    if symbol_density >= 0.18 and (digit_count + alpha_count) >= 3:
        return True
    if math_like >= 4 and chinese_count <= 4:
        return True
    if re.search(r'[A-Za-z]\s*=\s*', sample):
        return True
    if re.search(r'\b(?:sin|cos|tan|log|ln|max|min|argmax|argmin)\b', sample, flags=re.I):
        return True
    return False


def _replace_formula_like_spans(text: str) -> str:
    pieces = re.split(r'([。！？!?；;\n])', text)
    rebuilt: list[str] = []
    for piece in pieces:
        if not piece:
            continue
        if piece in '。！？!?；;\n':
            rebuilt.append('。')
            continue
        stripped = piece.strip()
        if not stripped:
            continue
        if _looks_formula_heavy(stripped):
            rebuilt.append(FORMULA_PLACEHOLDER)
        else:
            rebuilt.append(stripped)
    return ''.join(rebuilt)


def sanitize_text_for_tts(text: str) -> str:
    text = unicodedata.normalize('NFKC', text or '')
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'```.*?```', f' {CODE_PLACEHOLDER} ', text, flags=re.S)
    text = re.sub(r'`([^`]+)`', f' {CODE_PLACEHOLDER} ', text)
    text = re.sub(r'\$\$.*?\$\$', f' {FORMULA_PLACEHOLDER} ', text, flags=re.S)
    text = re.sub(r'\$([^$]+)\$', f' {FORMULA_PLACEHOLDER} ', text)
    text = re.sub(r'\\\[(.*?)\\\]', f' {FORMULA_PLACEHOLDER} ', text, flags=re.S)
    text = re.sub(r'\\\((.*?)\\\)', f' {FORMULA_PLACEHOLDER} ', text, flags=re.S)
    text = re.sub(r'!\[[^\]]*\]\([^)]+\)', ' 图片 ', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r' \1 ', text)
    text = re.sub(r'https?://\S+|www\.\S+', ' 链接 ', text)
    text = re.sub(r'(?m)^\s*\|.*\|\s*$', f' {TABLE_PLACEHOLDER} ', text)
    text = re.sub(r'(?m)^\s{0,3}#{1,6}\s*', '', text)
    text = re.sub(r'(?m)^\s*[-*+]\s+', '', text)
    text = re.sub(r'(?m)^\s*\d+[.)]\s+', '', text)
    text = re.sub(r'[_*~#>`]+', ' ', text)

    for source, target in SYMBOL_REPLACEMENTS:
        text = text.replace(source, f' {target} ')

    text = re.sub(r'(?<=\d)/(?=\d)', ' 比 ', text)
    text = re.sub(r'(?<=\d)-(?=\d)', ' 到 ', text)
    text = re.sub(r'(?<=\d)\+(?=\d)', ' 加 ', text)
    text = re.sub(r'(?<=\d)=(?=\d)', ' 等于 ', text)
    text = re.sub(r'[{}[\]<>|^~]', ' ', text)
    text = re.sub(r'([\\/]){2,}', ' ', text)
    text = re.sub(r'([=+\-_*#@~^])\1+', ' ', text)
    text = _replace_formula_like_spans(text)
    text = re.sub(rf'({FORMULA_PLACEHOLDER})(\s*{FORMULA_PLACEHOLDER})+', FORMULA_PLACEHOLDER, text)
    text = re.sub(rf'({CODE_PLACEHOLDER})(\s*{CODE_PLACEHOLDER})+', CODE_PLACEHOLDER, text)
    text = re.sub(rf'({TABLE_PLACEHOLDER})(\s*{TABLE_PLACEHOLDER})+', TABLE_PLACEHOLDER, text)
    text = re.sub(r'([。！？!?；;：:，,、]){2,}', r'\1', text)
    text = re.sub(r'\s*\n\s*', '。', text)
    text = re.sub(rf'\s*({FORMULA_PLACEHOLDER}|{CODE_PLACEHOLDER}|{TABLE_PLACEHOLDER})\s*', r' \1 ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def split_text(text: str, max_chars: int) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []

    pieces = re.split(r"(?<=[。！？!?；;：:，,、])", normalized)
    chunks: list[str] = []
    current = ""

    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        if len(piece) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(
                piece[index:index + max_chars]
                for index in range(0, len(piece), max_chars)
                if piece[index:index + max_chars].strip()
            )
            continue
        if len(current) + len(piece) <= max_chars:
            current += piece
        else:
            if current:
                chunks.append(current)
            current = piece

    if current:
        chunks.append(current)
    return chunks


def plan_batches(chunks: list[str], max_batch_size: int, max_batch_chars: int) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    current_chars = 0

    for chunk in chunks:
        if current and (len(current) >= max_batch_size or current_chars + len(chunk) > max_batch_chars):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(chunk)
        current_chars += len(chunk)

    if current:
        batches.append(current)
    return batches


def estimate_max_new_tokens(chunk: str, hard_cap: int) -> int:
    return min(hard_cap, max(256, len(chunk) * 12))


def pick_attn_implementation(value: str) -> str:
    value = (value or 'auto').strip().lower()
    if value != 'auto':
        return value
    if torch.cuda.is_available() and importlib.util.find_spec('flash_attn') is not None:
        return 'flash_attention_2'
    return 'sdpa'


class StreamingAudioWriter:
    def __init__(self, path: Path, audio_format: str, sr: int, mp3_bitrate: str, ffmpeg_bin: str | None = None):
        self.path = path
        self.audio_format = audio_format
        self.sr = sr
        self._sound_file = None
        self._proc = None

        if audio_format == 'wav':
            self._sound_file = sf.SoundFile(path, mode='w', samplerate=sr, channels=1, subtype='PCM_16')
            return

        ffmpeg_bin = ffmpeg_bin or (shutil.which('ffmpeg') if 'shutil' in globals() else None) or 'ffmpeg'
        cmd = [
            ffmpeg_bin,
            '-y',
            '-hide_banner',
            '-loglevel',
            'error',
            '-f',
            's16le',
            '-ac',
            '1',
            '-ar',
            str(sr),
            '-i',
            'pipe:0',
            '-vn',
            '-acodec',
            'libmp3lame',
            '-b:a',
            mp3_bitrate,
            '-compression_level',
            '0',
            str(path),
        ]
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    def write(self, audio: np.ndarray) -> None:
        wav = np.asarray(audio, dtype=np.float32)
        if wav.ndim > 1:
            wav = wav.mean(axis=-1, dtype=np.float32)
        wav = np.clip(wav, -1.0, 1.0)

        if self._sound_file is not None:
            self._sound_file.write(wav)
            return

        pcm16 = (wav * 32767.0).astype(np.int16, copy=False)
        assert self._proc is not None and self._proc.stdin is not None
        self._proc.stdin.write(pcm16.tobytes())

    def close(self) -> None:
        if self._sound_file is not None:
            self._sound_file.close()
            return
        if self._proc is None:
            return
        if self._proc.stdin is not None:
            self._proc.stdin.close()
            self._proc.stdin = None
        stderr = b''
        if self._proc.stderr is not None:
            stderr = self._proc.stderr.read()
            self._proc.stderr.close()
            self._proc.stderr = None
        self._proc.wait()
        if self._proc.returncode != 0:
            raise RuntimeError(f'ffmpeg mp3 encoding failed: {stderr.decode("utf-8", errors="ignore")}')


class QwenTTSRuntime:
    def __init__(
        self,
        model_size='0.6B',
        device=None,
        dtype_name=None,
        attn_implementation='auto',
        compile_mode=None,
        mp3_bitrate='128k',
        batch_size=8,
        max_batch_chars=2200,
        max_chars=700,
        max_new_tokens=DEFAULT_MAX_NEW_TOKENS,
        pause_ms=350,
        warmup=False,
        ffmpeg_bin=None,
    ):
        self.model_size = model_size
        self.device = device or ('cuda:0' if torch.cuda.is_available() else 'cpu')
        self.dtype_name = dtype_name or ('bfloat16' if self.device.startswith('cuda') else 'float32')
        self.attn_implementation = pick_attn_implementation(attn_implementation)
        self.compile_mode = (compile_mode or os.getenv('QWEN_TTS_COMPILE', 'off')).strip()
        self.mp3_bitrate = mp3_bitrate
        self.batch_size = max(1, batch_size)
        self.max_batch_chars = max(1, max_batch_chars)
        self.max_chars = max(1, max_chars)
        self.max_new_tokens = max(256, max_new_tokens)
        self.pause_ms = max(0, pause_ms)
        self.warmup = warmup
        self.ffmpeg_bin = ffmpeg_bin
        self.tts = None

    def prepare_text(self, text: str) -> tuple[str, list[str], int]:
        normalized_text = sanitize_text_for_tts(text)
        direct_max_chars = min(self.max_batch_chars, get_tts_runtime_rules()['direct_max_chars'])
        if len(normalized_text) <= direct_max_chars:
            chunks = [normalized_text] if normalized_text else []
        else:
            chunks = split_text(normalized_text, self.max_chars)
        return normalized_text, chunks, direct_max_chars

    def _torch_dtype(self):
        return {
            'bfloat16': torch.bfloat16,
            'float16': torch.float16,
            'float32': torch.float32,
        }[self.dtype_name]

    def _model_path(self):
        repo = f'Qwen/Qwen3-TTS-12Hz-{self.model_size}-CustomVoice'
        try:
            return snapshot_download(repo, local_files_only=True)
        except Exception:
            return snapshot_download(repo)

    def load(self, progress_callback=None):
        if self.tts is not None:
            return self.tts
        if progress_callback:
            progress_callback('locating_model_cache', repo=f'Qwen/Qwen3-TTS-12Hz-{self.model_size}-CustomVoice')
        model_path = self._model_path()
        if progress_callback:
            progress_callback(
                'loading_model',
                device=self.device,
                dtype=self.dtype_name,
                attn=self.attn_implementation,
                compile=self.compile_mode or 'off',
            )
        self.tts = Qwen3TTSModel.from_pretrained(
            model_path,
            device_map=self.device,
            dtype=self._torch_dtype(),
            attn_implementation=self.attn_implementation,
            compile_mode=self.compile_mode,
        )
        if progress_callback:
            progress_callback('model_loaded')
        return self.tts

    def _maybe_warmup(self, *, tts, chunks, language, speaker, instruct, progress_callback=None):
        if not self.warmup or not chunks:
            return
        warmup_text = chunks[0][: min(40, len(chunks[0]))]
        if not warmup_text:
            return
        if progress_callback:
            progress_callback('warmup', chars=len(warmup_text))
        with torch.inference_mode():
            self._reset_tts_inference_state(tts)
            tts.generate_custom_voice(
                text=[warmup_text],
                language=language,
                speaker=speaker,
                instruct=instruct,
                non_streaming_mode=True,
                max_new_tokens=min(384, self.max_new_tokens),
            )
        if torch.cuda.is_available():
            torch.cuda.synchronize()

    def _reset_tts_inference_state(self, tts) -> None:
        model = getattr(tts, 'model', None)
        talker = getattr(model, 'talker', None)
        if talker is not None and hasattr(talker, 'rope_deltas'):
            talker.rope_deltas = None

    def _generate_batch_audio(
        self,
        *,
        tts,
        batch_chunks,
        language,
        speaker,
        instruct,
        max_new_tokens,
        progress_callback=None,
        batch_index=None,
        total_batches=None,
    ):
        self._reset_tts_inference_state(tts)
        if len(batch_chunks) > 1:
            try:
                wavs, sample_rate = tts.generate_custom_voice(
                    text=batch_chunks,
                    language=[language] * len(batch_chunks),
                    speaker=[speaker] * len(batch_chunks),
                    instruct=[instruct] * len(batch_chunks),
                    non_streaming_mode=True,
                    max_new_tokens=max_new_tokens,
                )
                if len(wavs) != len(batch_chunks):
                    raise RuntimeError(
                        f'batch generation returned {len(wavs)} wavs for {len(batch_chunks)} chunks'
                    )
                return wavs, sample_rate
            except Exception as exc:
                if progress_callback:
                    progress_callback(
                        'batch_fallback',
                        batch_index=batch_index,
                        total_batches=total_batches,
                        items=len(batch_chunks),
                        reason=f'batch_generation_error:{exc.__class__.__name__}',
                    )

        all_wavs = []
        sample_rate = None
        for chunk in batch_chunks:
            self._reset_tts_inference_state(tts)
            chunk_wavs, chunk_sr = tts.generate_custom_voice(
                text=[chunk],
                language=language,
                speaker=speaker,
                instruct=instruct,
                non_streaming_mode=True,
                max_new_tokens=max_new_tokens,
            )
            if sample_rate is None:
                sample_rate = chunk_sr
            elif sample_rate != chunk_sr:
                raise RuntimeError(f'inconsistent sample rate in serial generation: {sample_rate} != {chunk_sr}')
            all_wavs.extend(chunk_wavs)
        if sample_rate is None:
            raise RuntimeError('serial generation returned no audio')
        return all_wavs, sample_rate

    def generate_batch_items(
        self,
        *,
        items,
        progress_callback=None,
        batch_index=None,
        total_batches=None,
    ):
        if not items:
            return [], 0

        tts = self.load()
        self._reset_tts_inference_state(tts)

        if len(items) > 1:
            try:
                wavs, sample_rate = tts.generate_custom_voice(
                    text=[item['text'] for item in items],
                    language=[item['language'] for item in items],
                    speaker=[item['speaker'] for item in items],
                    instruct=[item['instruct'] for item in items],
                    non_streaming_mode=True,
                    max_new_tokens=max(item['max_new_tokens'] for item in items),
                )
                if len(wavs) != len(items):
                    raise RuntimeError(f'batch generation returned {len(wavs)} wavs for {len(items)} items')
                return list(wavs), sample_rate
            except Exception as exc:
                if progress_callback:
                    progress_callback(
                        'batch_fallback',
                        batch_index=batch_index,
                        total_batches=total_batches,
                        items=len(items),
                        reason=f'batch_generation_error:{exc.__class__.__name__}',
                    )

        sample_rate = None
        results = []
        for item in items:
            self._reset_tts_inference_state(tts)
            try:
                wavs, current_sr = tts.generate_custom_voice(
                    text=[item['text']],
                    language=item['language'],
                    speaker=item['speaker'],
                    instruct=item['instruct'],
                    non_streaming_mode=True,
                    max_new_tokens=item['max_new_tokens'],
                )
                if len(wavs) != 1:
                    raise RuntimeError(f'serial generation returned {len(wavs)} wavs for one item')
                if sample_rate is None:
                    sample_rate = current_sr
                elif sample_rate != current_sr:
                    raise RuntimeError(f'inconsistent sample rate in serial generation: {sample_rate} != {current_sr}')
                results.append(wavs[0])
            except Exception as exc:
                results.append(exc)
        return results, sample_rate or 0

    def synthesize_to_file(
        self,
        *,
        text: str,
        output_path: Path,
        speaker: str,
        language: str,
        instruct: str,
        output_format: str = 'mp3',
        progress_callback=None,
        should_cancel=None,
    ) -> tuple[int, int]:
        normalized_text, chunks, direct_max_chars = self.prepare_text(text)
        batches = plan_batches(chunks, self.batch_size, self.max_batch_chars)
        total_chars = len(normalized_text)

        if progress_callback:
            progress_callback(
                'text_ready',
                chars=total_chars,
                chunks=len(chunks),
                batches=len(batches),
                direct_max_chars=direct_max_chars,
                chunk_size=self.max_chars,
                batch_chars=self.max_batch_chars,
            )

        tts = self.load(progress_callback=progress_callback)
        self._maybe_warmup(
            tts=tts,
            chunks=chunks,
            language=language,
            speaker=speaker,
            instruct=instruct,
            progress_callback=progress_callback,
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        writer = None
        total_audio_samples = 0
        total_started = time.perf_counter()
        sr = 0

        for batch_index, batch_chunks in enumerate(batches, start=1):
            if should_cancel and should_cancel():
                raise CancelRequestedError('订单已请求取消')

            batch_chars = sum(len(chunk) for chunk in batch_chunks)
            batch_max_new_tokens = max(estimate_max_new_tokens(chunk, self.max_new_tokens) for chunk in batch_chunks)
            if progress_callback:
                progress_callback(
                    'batch_start',
                    batch_index=batch_index,
                    total_batches=len(batches),
                    batch_chars=batch_chars,
                    items=len(batch_chunks),
                    max_new_tokens=batch_max_new_tokens,
                )

            if torch.cuda.is_available():
                torch.cuda.synchronize()
            batch_started = time.perf_counter()
            with torch.inference_mode():
                wavs, current_sr = self._generate_batch_audio(
                    tts=tts,
                    batch_chunks=batch_chunks,
                    language=language,
                    speaker=speaker,
                    instruct=instruct,
                    max_new_tokens=batch_max_new_tokens,
                    progress_callback=progress_callback,
                    batch_index=batch_index,
                    total_batches=len(batches),
                )
            if torch.cuda.is_available():
                torch.cuda.synchronize()
            batch_elapsed = time.perf_counter() - batch_started

            if should_cancel and should_cancel():
                raise CancelRequestedError('订单已请求取消')

            if writer is None:
                sr = current_sr
                writer = StreamingAudioWriter(
                    output_path,
                    audio_format=output_format,
                    sr=sr,
                    mp3_bitrate=self.mp3_bitrate,
                    ffmpeg_bin=self.ffmpeg_bin,
                )
                pause = np.zeros(int(sr * self.pause_ms / 1000), dtype=np.float32) if self.pause_ms else None

            batch_audio_samples = 0
            for item_index, wav in enumerate(wavs, start=1):
                wav = np.asarray(wav, dtype=np.float32)
                writer.write(wav)
                total_audio_samples += len(wav)
                batch_audio_samples += len(wav)

                is_last_batch = batch_index == len(batches)
                is_last_item = item_index == len(wavs)
                if pause is not None and not (is_last_batch and is_last_item):
                    writer.write(pause)
                    total_audio_samples += len(pause)
                    batch_audio_samples += len(pause)

            if progress_callback:
                progress_callback(
                    'batch_done',
                    batch_index=batch_index,
                    total_batches=len(batches),
                    gen_sec=batch_elapsed,
                    audio_sec=(batch_audio_samples / sr) if sr else 0,
                )

        if writer is None:
            raise ValueError('待转文本为空')

        if progress_callback:
            progress_callback('finalizing_audio')
        writer.close()

        duration_seconds = int(total_audio_samples / sr) if sr else 0
        if progress_callback:
            progress_callback(
                'completed',
                elapsed_sec=time.perf_counter() - total_started,
                audio_sec=(total_audio_samples / sr) if sr else 0,
            )
        return sr or 0, duration_seconds

    def unload(self):
        if self.tts is not None:
            del self.tts
            self.tts = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            gc.collect()
