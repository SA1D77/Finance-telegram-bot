"""Offline recognisers: Vosk for speech (with ffmpeg), Tesseract for receipts. Nothing leaves the machine.

Both are optional: they are installed into the image only when FEATURE_VOICE / FEATURE_RECEIPTS
are on (see start.bat and the Dockerfile build args).
"""
import io
import json
import os
import shutil
import subprocess
import tempfile
from functools import lru_cache

TIMEOUT_SECONDS = 60


class EngineUnavailable(Exception):
    """The recogniser is not installed in this image."""


def ogg_to_pcm(data: bytes) -> bytes:
    """Telegram voice (OGG/Opus) -> 16 kHz mono 16-bit PCM for Vosk."""
    if shutil.which('ffmpeg') is None:
        raise EngineUnavailable('ffmpeg')
    result = subprocess.run(['ffmpeg', '-loglevel', 'error', '-i', 'pipe:0', '-ar', '16000', '-ac', '1', '-f', 's16le', 'pipe:1'],
                            input=data, capture_output=True, timeout=TIMEOUT_SECONDS, check=True)
    return result.stdout


@lru_cache(maxsize=1)
def _vosk_model(path: str):
    import vosk
    vosk.SetLogLevel(-1)
    return vosk.Model(path)


def transcribe(data: bytes, model_path: str) -> str:
    try:
        import vosk
    except ImportError as error:
        raise EngineUnavailable('vosk') from error
    if not os.path.isdir(model_path):
        raise EngineUnavailable('vosk model')
    recognizer = vosk.KaldiRecognizer(_vosk_model(model_path), 16000)
    recognizer.AcceptWaveform(ogg_to_pcm(data))
    return json.loads(recognizer.FinalResult()).get('text', '')


def ocr(image: bytes, languages: str) -> str:
    """Receipt photo -> text. The image is converted to large grayscale first, which helps Tesseract a lot."""
    if shutil.which('tesseract') is None:
        raise EngineUnavailable('tesseract')
    from PIL import Image, ImageOps

    picture = ImageOps.exif_transpose(Image.open(io.BytesIO(image))).convert('L')
    if picture.width < 1500:
        scale = 1500 / picture.width
        picture = picture.resize((1500, int(picture.height * scale)))
    with tempfile.TemporaryDirectory(prefix='receipt-') as directory:
        path = os.path.join(directory, 'receipt.png')
        picture.save(path)
        result = subprocess.run(['tesseract', path, 'stdout', '-l', languages, '--psm', '6'],
                                capture_output=True, timeout=TIMEOUT_SECONDS, check=True)
    return result.stdout.decode('utf-8', errors='replace')
