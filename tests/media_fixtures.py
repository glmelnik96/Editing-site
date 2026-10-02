"""Короткие ролики, которые ffmpeg генерирует сам: тесты не тащат бинарные файлы в репозиторий."""
from __future__ import annotations

import shutil
import subprocess
from functools import cache
from pathlib import Path

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
HAVE_FFMPEG = bool(FFMPEG and FFPROBE)


def make_video(path: Path, *, seconds: int = 6, size: str = "960x540", silent_from: float = 2.0,
               silent_to: float = 4.0) -> Path:
    """Видео со звуком, в середине участок тишины: на нём проверяется карта пауз.

    Кадр больше 640 px по длинной стороне: так прокси реально уменьшается, а не остаётся 1:1.
    """
    mute = f"volume=enable='between(t,{silent_from},{silent_to})':volume=0"
    subprocess.run(
        [
            FFMPEG, "-v", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:duration={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-filter_complex", f"[1:a]{mute}[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def make_silent_video(path: Path, *, seconds: int = 3) -> Path:
    subprocess.run(
        [
            FFMPEG, "-v", "error", "-y", "-f", "lavfi",
            "-i", f"testsrc2=size=160x120:rate=25:duration={seconds}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def make_audio(path: Path, *, seconds: int = 4) -> Path:
    subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}",
         "-c:a", "aac", str(path)],
        check=True,
        capture_output=True,
    )
    return path


def make_broken(path: Path) -> Path:
    path.write_bytes(b"not a video at all" * 100)
    return path


@cache
def have_encoders(*names: str) -> bool:
    """Есть ли кодеки в сборке ffmpeg: VP8 и Opus внешние, их может не оказаться."""
    if not HAVE_FFMPEG:
        return False
    listing = subprocess.run(
        [FFMPEG, "-v", "error", "-encoders"], capture_output=True, text=True, check=False
    ).stdout
    found = set(listing.split())
    return all(name in found for name in names)


def make_browser_webm(path: Path, *, seconds: int = 4) -> Path:
    """WebM, как его пишет запись экрана или камеры в браузере: потоком, без длительности и индекса.

    -live 1 и вывод в трубу — так же поступает MediaRecorder: заголовок уходит раньше, чем
    известна длина, и вернуться дописать её некуда."""
    with open(path, "wb") as out:
        subprocess.run(
            [
                FFMPEG, "-v", "error", "-y",
                "-t", str(seconds), "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25",
                "-t", str(seconds), "-f", "lavfi", "-i", "sine=frequency=440",
                "-c:v", "libvpx", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "300k",
                "-c:a", "libopus", "-live", "1", "-f", "webm", "pipe:1",
            ],
            check=True,
            stdout=out,
            stderr=subprocess.PIPE,
        )
    return path
