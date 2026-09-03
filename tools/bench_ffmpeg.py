"""Замер скорости ffmpeg на этой машине: прокси / draft / final по пресетам спеки.

Запуск на VM из корня репо:  .venv/bin/python tools/bench_ffmpeg.py
Образец 4K генерируется сам (testsrc2 + sine, 60 с), либо передаётся свой файл: --sample path.mp4
Отчёт пишется в docs/benchmarks/<дата>-<хост>.md
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

PROXY_SCALE = "scale=w='if(gte(iw,ih),854,-2)':h='if(gte(iw,ih),-2,854)'"


def sample_command(path: Path, seconds: int) -> list[str]:
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=3840x2160:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", str(seconds),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path),
    ]


def bench_commands(sample: Path, out_dir: Path) -> dict[str, list[str]]:
    common = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(sample)]
    return {
        "proxy": common + [
            "-vf", PROXY_SCALE, "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
            "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out_dir / "proxy.mp4"),
        ],
        "draft": common + [
            "-vf", "scale=-2:720", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", str(out_dir / "draft.mp4"),
        ],
        "final": common + [
            "-vf", "scale=-2:1080", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
            str(out_dir / "final.mp4"),
        ],
    }


def realtime_factor(media_seconds: float, elapsed_seconds: float) -> float:
    if elapsed_seconds <= 0:
        raise ValueError("elapsed_seconds must be > 0")
    return round(media_seconds / elapsed_seconds, 2)


def render_report(host: str, cpu_count: int, media_seconds: float, results: dict[str, float]) -> str:
    lines = [
        f"# Замер ffmpeg: {host}",
        "",
        f"- Дата: {date.today().isoformat()}",  # noqa: DTZ011
        f"- CPU: {cpu_count} потоков",
        f"- Образец: {media_seconds:.0f} с, 3840x2160, 30 fps",
        "",
        "| Задача | Время, с | Быстрее реального времени |",
        "|---|---|---|",
    ]
    for name, elapsed in results.items():
        lines.append(f"| {name} | {elapsed:.1f} | {realtime_factor(media_seconds, elapsed)}× |")
    return "\n".join(lines) + "\n"


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return float(out)


def timed(cmd: list[str]) -> float:
    started = time.perf_counter()
    subprocess.run(cmd, check=True)
    return time.perf_counter() - started


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--sample", type=Path, help="свой файл вместо сгенерированного образца")
    parser.add_argument("--seconds", type=int, default=60, help="длина генерируемого образца")
    parser.add_argument("--work", type=Path, default=Path("data/bench"), help="рабочий каталог")
    parser.add_argument("--out", type=Path, default=Path("docs/benchmarks"), help="куда писать отчёт")
    args = parser.parse_args(argv)

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("ffmpeg/ffprobe не найдены в PATH", file=sys.stderr)
        return 2
    args.work.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)

    if args.sample is None:
        sample = args.work / "sample_4k.mp4"
        print(f"генерирую образец {args.seconds} с 4K…", flush=True)
        subprocess.run(sample_command(sample, args.seconds), check=True)
    else:
        sample = args.sample
    media_seconds = probe_duration(sample)

    results: dict[str, float] = {}
    for name, cmd in bench_commands(sample, args.work).items():
        print(f"{name}…", end=" ", flush=True)
        results[name] = timed(cmd)
        print(f"{results[name]:.1f} с", flush=True)

    host = platform.node() or "unknown"
    report = render_report(host, os.cpu_count() or 0, media_seconds, results)
    path = args.out / f"{date.today().isoformat()}-{host}.md"  # noqa: DTZ011
    path.write_text(report, encoding="utf-8")
    print()
    print(report)
    print("отчёт:", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
