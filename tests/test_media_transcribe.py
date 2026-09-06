"""План нарезки и аргументы ffmpeg. Последние тесты режут настоящий звук — идут пару секунд."""
import json
import subprocess
from itertools import pairwise
from pathlib import Path

import pytest

from server.app.config import Settings
from server.media.run import MediaError
from server.media.transcribe import MIN_CHUNK_BYTES, check_chunk_size, chunk_args, chunk_plan
from tests.media_fixtures import FFMPEG, FFPROBE, HAVE_FFMPEG

S = Settings(_env_file=None)
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="нужен ffmpeg в PATH")


def value_of(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


def test_short_audio_is_one_chunk():
    assert chunk_plan(duration=120.0, silences=[], target=600, window=60) == [(0.0, 120.0)]


def test_boundary_goes_to_the_nearest_pause():
    """Граница на паузе не рвёт слово пополам: в окне ±60 с от цели ищем ближайшую тишину
    и режем по её середине."""
    silences = [{"start": 570.0, "end": 572.0}, {"start": 640.0, "end": 641.0}]
    plan = chunk_plan(duration=1200.0, silences=silences, target=600, window=60)
    assert plan[0] == (0.0, 571.0)
    assert plan[1][0] == 571.0


def test_hard_cut_when_no_pause_in_window():
    plan = chunk_plan(duration=1200.0, silences=[], target=600, window=60)
    assert plan[0] == (0.0, 600.0) and plan[1] == (600.0, 1200.0)


def test_pause_outside_the_window_is_ignored():
    """Пауза за окном не годится: чанк уехал бы далеко от цели и мог превысить предел загрузки."""
    silences = [{"start": 100.0, "end": 120.0}]
    plan = chunk_plan(duration=1200.0, silences=silences, target=600, window=60)
    assert plan[0] == (0.0, 600.0)


def test_tail_is_not_a_crumb():
    """Огрызок в конце приклеивается к предыдущему чанку: отдельный запрос ради десяти секунд
    не окупается ни временем, ни риском лишнего шва."""
    plan = chunk_plan(duration=610.0, silences=[], target=600, window=60)
    assert plan == [(0.0, 610.0)]


def test_plan_covers_the_whole_audio_without_gaps():
    silences = [{"start": 595.0, "end": 596.0}, {"start": 1150.0, "end": 1152.0}]
    plan = chunk_plan(duration=1800.0, silences=silences, target=600, window=60)
    assert plan[0][0] == 0.0 and plan[-1][1] == 1800.0
    for before, after in pairwise(plan):
        assert before[1] == after[0], "между чанками не должно быть ни зазора, ни нахлёста"


def test_the_same_pause_does_not_cut_twice():
    """Пауза позади начала чанка в окно попадает, но границей быть не может: чанк вышел бы
    нулевой или отрицательной длины. При короткой цели это не теория, а обычный случай."""
    plan = chunk_plan(duration=400.0, silences=[{"start": 88.0, "end": 92.0}], target=30, window=60)
    assert plan[0] == (0.0, 90.0) and plan[1] == (90.0, 120.0)
    assert all(end > start for start, end in plan)


def test_empty_audio_has_nothing_to_cut():
    assert chunk_plan(duration=0.0, silences=[], target=600, window=60) == []


def test_chunk_args_encode_a_small_mono_mp3():
    args = chunk_args(S, src="/d/a/audio16k.wav", dst="/d/a/chunk-1.mp3", start=0.0, end=600.0)
    assert args[0] == S.ffmpeg_path and args[-1] == "/d/a/chunk-1.mp3"
    assert value_of(args, "-i") == "/d/a/audio16k.wav"
    assert value_of(args, "-c:a") == "libmp3lame" and value_of(args, "-b:a") == "64k"
    assert value_of(args, "-ar") == "16000" and value_of(args, "-ac") == "1"
    # Формат по расширению .mp3: -f не задаём, поэтому имя назначения не может быть временным.
    assert "-f" not in args


def test_cut_happens_after_decoding():
    """-ss и -to после -i: поиск по ключевым кадрам быстрее, но мажет, а времена идут в транскрипт."""
    args = chunk_args(S, src="src.wav", dst="dst.mp3", start=10.5, end=20.25)
    assert args.index("-i") < args.index("-ss") < args.index("-to")
    assert value_of(args, "-ss") == "10.500" and value_of(args, "-to") == "20.250"


def test_empty_chunk_is_a_slicing_error(tmp_path):
    """Килобайтный порог ловит файл с одним заголовком: провайдер вернул бы пустой текст,
    а в транскрипте появилась бы дыра на целый чанк."""
    good = tmp_path / "chunk-0.mp3"
    good.write_bytes(b"\0" * MIN_CHUNK_BYTES)
    assert check_chunk_size(good) == MIN_CHUNK_BYTES

    empty = tmp_path / "chunk-1.mp3"
    empty.write_bytes(b"\0" * 100)
    with pytest.raises(MediaError) as exc:
        check_chunk_size(empty)
    assert exc.value.reason == "empty_chunk"

    with pytest.raises(MediaError):
        check_chunk_size(tmp_path / "chunk-2.mp3")


def make_speech_like_audio(path: Path, *, seconds: int = 30) -> Path:
    """Тон 16 кГц моно с двумя паузами: по ним план и режет, как по настоящей речи."""
    mute = "volume=enable='between(t,9,11)':volume=0,volume=enable='between(t,19,21)':volume=0"
    subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-af", mute, "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(path)],
        check=True,
        capture_output=True,
    )
    return path


def audio_facts(path: Path) -> dict:
    raw = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "a:0", "-of", "json",
         "-show_entries", "stream=sample_rate,channels:format=duration", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    data = json.loads(raw)
    stream = (data.get("streams") or [{}])[0]
    return {
        "rate": int(stream["sample_rate"]),
        "channels": int(stream["channels"]),
        "duration": float(data["format"]["duration"]),
    }


@needs_ffmpeg
def test_real_slicing_covers_the_source(tmp_path):
    source = make_speech_like_audio(tmp_path / "audio16k.wav")
    total = audio_facts(source)["duration"]
    silences = [{"start": 9.0, "end": 11.0}, {"start": 19.0, "end": 21.0}]
    plan = chunk_plan(duration=total, silences=silences, target=10, window=3)
    assert len(plan) == 3

    parts = []
    for number, (start, end) in enumerate(plan):
        dst = tmp_path / f"chunk-{number}.mp3"
        subprocess.run(chunk_args(S, src=str(source), dst=str(dst), start=start, end=end),
                       check=True, capture_output=True)
        assert check_chunk_size(dst) > MIN_CHUNK_BYTES
        parts.append(audio_facts(dst))

    assert sum(p["duration"] for p in parts) == pytest.approx(total, abs=0.2)
    assert all(p["rate"] == 16000 and p["channels"] == 1 for p in parts)
