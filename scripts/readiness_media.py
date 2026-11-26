"""Bounded local media adapters, chunking, OCR/ASR hooks, and generated non-professional fixtures."""
import csv
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess
import tempfile
import wave
import zipfile
import zlib

from platform_fixtures import write_fixture
from platform_parsers import parse_file, Unavailable

DOCUMENTS = {'.txt', '.md', '.docx', '.html', '.htm', '.csv', '.json', '.pptx', '.xlsx', '.pdf'}
CHUNKABLE = {'.txt', '.md', '.docx', '.html', '.htm', '.pdf'}
IMAGES = {'.png', '.jpg', '.jpeg'}
AUDIO = {'.wav', '.mp3', '.m4a'}
VIDEO = {'.mp4', '.mov'}
SUFFIXES = DOCUMENTS | IMAGES | AUDIO | VIDEO
MAX_MEDIA_EXTRACT_SECONDS = 600


def ffmpeg():
    command = shutil.which('ffmpeg')
    if not command and importlib.util.find_spec('imageio_ffmpeg'):
        from imageio_ffmpeg import get_ffmpeg_exe
        try:
            command = get_ffmpeg_exe()
        except RuntimeError as exc:
            raise Unavailable('FFmpeg executable unavailable') from exc
    if not command:
        raise Unavailable('Install FFmpeg or the optional imageio-ffmpeg package')
    return command


def run_media(*args, timeout=60):
    result = subprocess.run([ffmpeg(), '-nostdin', '-hide_banner', '-protocol_whitelist', 'file,pipe',
                             *map(str, args)], stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
    if result.returncode:
        raise ValueError('FFmpeg failed: ' + result.stderr.decode('utf8', 'replace')[-400:])
    return result


def probe_media(path):
    info = run_media('-i', path, '-map', '0', '-t', '0.01', '-f', 'null', '-').stderr.decode('utf8', 'replace')
    duration = re.search(r'Duration: (\d+):(\d+):([\d.]+)', info)
    if not duration:
        raise ValueError('No duration in media metadata')
    h, m, s = map(float, duration.groups())
    end = round((3600 * h + 60 * m + s) * 1000)
    streams = [l.strip() for l in info.splitlines() if 'Stream #' in l][:8]
    return {
        'duration_ms': end,
        'streams': streams,
        'has_audio': any('Audio:' in s for s in streams),
        'has_video': any('Video:' in s for s in streams),
    }


def image_pixels(path):
    """Fixed visible stripe fixture. Recognition decodes the emitted RGB pixels."""
    rows = [bytes(rgb for x in range(96) for rgb in ((35, 86, 155) if 12 <= x < 84 and 8 <= y < 24
                                                     else (255, 255, 255))) for y in range(32)]
    def chunk(tag, data):
        return struct.pack('!I', len(data)) + tag + data + struct.pack('!I', zlib.crc32(tag + data))
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 96, 32, 8, 2, 0, 0, 0))
                     + chunk(b'IDAT', zlib.compress(b''.join(b'\0' + r for r in rows))) + chunk(b'IEND', b''))


def fixture_pdf(path):
    stream = b'BT /F1 18 Tf 25 105 Td (Synthetic document) Tj ET'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 150] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'\nendstream']
    payload = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(payload))
        payload.extend(b'%d 0 obj\n' % i + obj + b'\nendobj\n')
    start = len(payload)
    payload.extend(b'xref\n0 6\n0000000000 65535 f \n' + b''.join(b'%010d 00000 n \n' % v for v in offsets[1:]))
    payload.extend(b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % start)
    path.write_bytes(payload)


def generate(path, label):
    ext = path.suffix
    if ext == '.txt':
        path.write_text('Synthetic | ' + label + '\nFixture token. No professional guidance.\n', encoding='utf8')
    elif ext == '.htm':
        path.write_text('<h1>Synthetic fixture</h1>', encoding='utf8')
    elif ext == '.pdf':
        fixture_pdf(path)
    elif ext in IMAGES:
        if ext == '.png':
            image_pixels(path)
        else:
            from PIL import Image
            with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
                png = Path(tmp) / 'source.png'
                image_pixels(png)
                with Image.open(png) as image:
                    image.save(path, 'JPEG')
    elif ext == '.wav':
        # Square wave on for 200 ms followed by silence, twice, at 16 kHz.
        cycle = struct.pack('<32h', *([4000] * 16 + [-4000] * 16))
        with wave.open(str(path), 'wb') as out:
            out.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            out.writeframes((cycle * 100 + b'\0\0' * 3200) * 2)
    elif ext in AUDIO | VIDEO:
        with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
            audio = Path(tmp) / 'source.wav'
            generate(audio, label)
            if ext in AUDIO:
                run_media('-loglevel', 'error', '-i', audio, '-y', path)
            else:
                png = Path(tmp) / 'source.png'
                image_pixels(png)
                run_media('-loglevel', 'error', '-loop', '1', '-framerate', '10', '-i', png,
                          '-i', audio, '-t', '0.8', '-c:v', 'mpeg4', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-y', path)
    else:
        write_fixture(path)


def generate_silent_video(path, duration_s=0.6):
    """Generate a synthetic video with NO audio stream for silent-clip regression tests."""
    with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
        png = Path(tmp) / 'source.png'
        image_pixels(png)
        run_media('-loglevel', 'error', '-loop', '1', '-framerate', '10', '-i', png,
                  '-t', str(duration_s), '-an', '-c:v', 'mpeg4', '-pix_fmt', 'yuv420p', '-y', path)


def check_archive(path):
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) > 5000 or sum(m.file_size for m in members) > 50_000_000:
            raise ValueError('Archive entry or expanded size limit')
        names = set()
        for member in members:
            name = member.filename
            if (name in names or ':' in name or '\\' in name or PurePosixPath(name).is_absolute()
                    or '..' in PurePosixPath(name).parts or member.flag_bits & 1):
                raise ValueError('Unsafe archive member')
            names.add(name)


def pdf_parts(path, page_size=20):
    from pypdf import PdfReader
    reader = PdfReader(path)
    for start in range(0, len(reader.pages), page_size):
        yield start, min(start + page_size, len(reader.pages))


def pdf_pages(path, start, end):
    from pypdf import PdfReader
    reader = PdfReader(path)
    spans = []
    for index in range(start, end):
        page = reader.pages[index]
        box = [float(v) for v in page.mediabox]
        content = page.extract_text() or ''
        spans.append(segment('pypdf-native', content, {'type': 'page', 'page': index + 1, 'media_box': box,
                             'rotation': page.rotation}, metadata={'ocr_required': not bool(content.strip())}))
    return spans


def chunk_segments(segments, max_chars=800, overlap=100):
    """Deterministically group or split native segments into bounded chunks with source locator ranges."""
    if not isinstance(max_chars, int) or max_chars < 50 or not 0 <= overlap < max_chars:
        raise ValueError('Invalid chunking parameters')
    non_empty = [s for s in segments if (s.get('extracted_content') or '').strip()]
    if not non_empty:
        return []
    chunks = []
    buf_texts = []
    buf_locs = []
    buf_len = 0

    def flush():
        nonlocal buf_texts, buf_locs, buf_len
        if not buf_texts:
            return
        combined = '\n'.join(buf_texts)
        chunks.append(segment(
            'chunker-v1',
            combined,
            {
                'type': 'chunk',
                'chunk_index': len(chunks) + 1,
                'start_locator': buf_locs[0],
                'end_locator': buf_locs[-1],
                'span_count': len(buf_locs),
            },
        ))
        if overlap > 0 and len(combined) > overlap:
            tail = combined[-overlap:]
            buf_texts = [tail]
            buf_locs = [buf_locs[-1]]
            buf_len = len(tail)
        else:
            buf_texts, buf_locs, buf_len = [], [], 0

    for item in non_empty:
        text = item['extracted_content'].strip()
        loc = item['locator']
        if len(text) > max_chars:
            flush()
            step = max_chars - overlap
            for offset in range(0, len(text), step):
                piece = text[offset:offset + max_chars]
                if not piece:
                    continue
                chunks.append(segment(
                    'chunker-v1',
                    piece,
                    {
                        'type': 'chunk',
                        'chunk_index': len(chunks) + 1,
                        'start_locator': loc,
                        'end_locator': loc,
                        'char_start': offset,
                        'char_end': offset + len(piece),
                    },
                ))
            buf_texts, buf_locs, buf_len = [], [], 0
            continue
        if buf_texts and buf_len + len(text) + 1 > max_chars:
            flush()
        buf_texts.append(text)
        buf_locs.append(loc)
        buf_len += len(text) + 1
    flush()
    return chunks


def asr_vtt(path, spans):
    def timecode(ms):
        sec, milli = divmod(ms, 1000)
        mins, sec = divmod(sec, 60)
        hour, mins = divmod(mins, 60)
        return f'{hour:02}:{mins:02}:{sec:02}.{milli:03}'
    rows = ['WEBVTT', '']
    for item in spans:
        loc = item['locator']
        rows += [f"{timecode(loc['start_ms'])} --> {timecode(loc['end_ms'])}", item['extracted_content'], '']
    path.write_text('\n'.join(rows) + '\n', encoding='utf8')


def native(path, source, revision, output):
    if path.suffix in {'.docx', '.pptx', '.xlsx'}:
        check_archive(path)
    if path.suffix in AUDIO | VIDEO and path.suffix != '.wav':
        meta = probe_media(path)
        return [segment('media-metadata', json.dumps(meta), {'start_ms': 0, 'end_ms': meta['duration_ms']})]
    return parse_file(path, source, revision)['segments']


def chunk_document(path, source, revision, output):
    base = pdf_pages(path, 0, len(list(pdf_parts(path, page_size=1)))) if path.suffix == '.pdf' else native(path, source, revision, output)
    return chunk_segments(base)


def segment(engine, content, locator, **extra):
    return {'extraction_method': engine, 'extracted_content': content, 'locator': locator, **extra}


def synthetic_ocr(path, source, revision, output):
    from PIL import Image
    with Image.open(path) as image:
        if image.width * image.height > 2_000_000:
            raise ValueError('Synthetic OCR pixel limit')
        colors = image.convert('RGB')
        points = [(x, y) for y in range(colors.height) for x in range(colors.width)
                  if (pixel := colors.getpixel((x, y)))[2] - pixel[0] > 70]
    if not points:
        return []
    x, y = zip(*points)
    return [segment('synthetic-ocr', 'Synthetic blue stripe', {'bbox': [min(x), min(y), max(x) + 1, max(y) + 1]})]


def tesseract_ocr(path, source, revision, output, lang='chi_sim+eng'):
    binary = shutil.which('tesseract')
    if not binary:
        raise Unavailable('Tesseract executable unavailable')
    langs_proc = subprocess.run([binary, '--list-langs'], capture_output=True, timeout=10)
    installed = set(langs_proc.stdout.decode('utf8', 'replace').splitlines()) if langs_proc.returncode == 0 else set()
    requested = [l for l in lang.split('+') if l in installed] if installed else []
    cmd = [binary, str(path), 'stdout', *(['-l', '+'.join(requested)] if requested else []), 'tsv']
    result = subprocess.run(cmd, capture_output=True, check=True, timeout=30)
    spans = []
    for row in csv.DictReader(result.stdout.decode('utf8').splitlines(), delimiter='\t'):
        if (row.get('text') or '').strip():
            x, y, w, h = (int(row[k]) for k in ('left', 'top', 'width', 'height'))
            spans.append(segment('tesseract', row['text'], {'bbox': [x, y, x + w, y + h]}, confidence=float(row['conf']) / 100))
    return spans


def synthetic_asr(path, source, revision, output):
    if path.suffix != '.wav':
        wav_path = output / 'asr-audio.wav'
        run_media('-loglevel', 'error', '-i', path, '-vn', '-ac', '1', '-ar', '16000',
                  '-t', str(MAX_MEDIA_EXTRACT_SECONDS), '-y', wav_path)
        path = wav_path
    spans = []
    with wave.open(str(path), 'rb') as audio:
        if audio.getsampwidth() != 2 or audio.getnchannels() != 1:
            raise ValueError('Synthetic ASR expects PCM16 mono')
        rate = audio.getframerate()
        length = max(1, rate // 50)
        current = None
        i = 0
        while data := audio.readframes(length):
            on = any(abs(v[0]) > 2000 for v in struct.iter_unpack('<h', data))
            if on and current is None:
                current = i
            elif not on and current is not None:
                spans.append(segment('synthetic-asr', 'Synthetic tone', {'track': 0, 'start_ms': 20 * current, 'end_ms': 20 * i}))
                current = None
            i += 1
        if current is not None:
            spans.append(segment('synthetic-asr', 'Synthetic tone', {'track': 0, 'start_ms': 20 * current, 'end_ms': 20 * i}))
    if path.name == 'asr-audio.wav':
        cues = output / 'captions.vtt'
        asr_vtt(cues, spans)
        for span in spans:
            span['file'] = cues.name
    return spans


def whisper_asr(path, source, revision, output, model_size='base'):
    """Real local Whisper ASR adapter (supports faster-whisper Python package or whisper CLI)."""
    has_fw = importlib.util.find_spec('faster_whisper') is not None
    cli = shutil.which('whisper')
    if not has_fw and not cli:
        raise Unavailable('Install faster-whisper or whisper CLI for local ASR')
    audio_path = path
    if path.suffix != '.wav':
        audio_path = output / 'whisper-audio.wav'
        run_media('-loglevel', 'error', '-i', path, '-vn', '-ac', '1', '-ar', '16000',
                  '-t', str(MAX_MEDIA_EXTRACT_SECONDS), '-y', audio_path)
    spans = []
    if has_fw:
        from faster_whisper import WhisperModel
        model = WhisperModel(model_size, device='cpu', compute_type='int8')
        segments_iter, _ = model.transcribe(str(audio_path), vad_filter=True)
        for seg in segments_iter:
            text = (seg.text or '').strip()
            if text:
                spans.append(segment('faster-whisper', text,
                                     {'track': 0, 'start_ms': round(seg.start * 1000), 'end_ms': round(seg.end * 1000)}))
    else:
        subprocess.run([cli, str(audio_path), '--model', model_size, '--output_format', 'json',
                        '--output_dir', str(output)], check=True, capture_output=True, timeout=300)
        json_out = output / (audio_path.stem + '.json')
        data = json.loads(json_out.read_text(encoding='utf8'))
        for seg in data.get('segments', []):
            text = (seg.get('text') or '').strip()
            if text:
                spans.append(segment('whisper-cli', text,
                                     {'track': 0, 'start_ms': round(seg['start'] * 1000), 'end_ms': round(seg['end'] * 1000)}))
    if spans:
        cues = output / 'whisper-captions.vtt'
        asr_vtt(cues, spans)
        for span in spans:
            span['file'] = cues.name
    return spans


def video_tracks(path, source, revision, output):
    meta = probe_media(path)
    length = meta['duration_ms']
    frame = output / 'frame-0.png'
    run_media('-loglevel', 'error', '-i', path, '-frames:v', '1', '-threads', '1', '-y', frame)
    spans = [segment('ffmpeg-keyframe', 'Extracted keyframe', {'time_ms': 0}, file=frame.name)]
    if meta['has_audio']:
        wav_path = output / 'audio.wav'
        run_media('-loglevel', 'error', '-i', path, '-vn', '-ac', '1', '-ar', '16000',
                  '-t', str(MAX_MEDIA_EXTRACT_SECONDS), '-y', wav_path)
        with wave.open(str(wav_path), 'rb') as wav:
            actual_end = min(length, round(1000 * wav.getnframes() / wav.getframerate()))
        spans.append(segment('ffmpeg-audio', 'Extracted audio track',
                             {'track': 0, 'start_ms': 0, 'end_ms': actual_end}, file=wav_path.name))
    return spans


class ParserRegistry:
    """Routes are code-owned and independently replaceable. Authorization is separate."""
    def __init__(self):
        self.routes = {ext: {'native': (native, ())} for ext in SUFFIXES}
        for ext in CHUNKABLE:
            self.register(ext, 'chunk', chunk_document)
        for ext in IMAGES:
            self.register(ext, 'ocr', synthetic_ocr, 'ocr')
            self.register(ext, 'tesseract', tesseract_ocr, 'ocr')
        for ext in AUDIO | VIDEO:
            video_perm = ['video_processing'] if ext in VIDEO else []
            self.register(ext, 'asr', synthetic_asr, 'asr', *video_perm)
            self.register(ext, 'whisper', whisper_asr, 'asr', *video_perm)
        for ext in VIDEO:
            self.register(ext, 'tracks', video_tracks, 'video_processing')

    def register(self, suffix, stage, adapter, *permissions):
        self.routes.setdefault(suffix, {})[stage] = (adapter, permissions)

    def route(self, suffix, stage):
        try:
            return self.routes[suffix][stage]
        except KeyError:
            raise Unavailable('No registered parser route for ' + suffix + '/' + stage) from None
