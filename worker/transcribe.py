"""Optional offline speech-to-text via a locally installed whisper.cpp binary.

If the binary/model is missing the worker does NOT crash: it asks the user to type the note.
"""
import os
import shutil
import subprocess


class TranscriptionUnavailable(Exception):
    pass


def transcribe(path):
    binary = os.environ.get("WHISPER_BIN") or shutil.which("whisper-cli") or shutil.which("main")
    model = os.environ.get("WHISPER_MODEL")
    if not binary or not model or not os.path.exists(model):
        raise TranscriptionUnavailable("whisper.cpp binary/model not found")
    if not os.path.exists(path):
        raise TranscriptionUnavailable("audio file not found")
    try:
        r = subprocess.run([binary, "-m", model, "-f", path, "-nt", "-l", "auto"],
                           capture_output=True, text=True, timeout=120)
    except Exception as e:
        raise TranscriptionUnavailable(type(e).__name__)
    text = (r.stdout or "").strip()
    if r.returncode != 0 or not text:
        raise TranscriptionUnavailable("empty transcript")
    return text
