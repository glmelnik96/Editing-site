"""Чистая часть транскрипции: план нарезки, аргументы ffmpeg, разбор ответа провайдера.

Тест с настоящей нарезкой звука идёт пару секунд, остальные — арифметика на фикстурах.
"""
import json
import subprocess
from itertools import pairwise
from pathlib import Path

import pytest

from server.app.config import Settings
from server.media.run import MediaError
from server.media.transcribe import (
    MIN_CHUNK_BYTES,
    check_chunk_size,
    chunk_args,
    chunk_plan,
    clamp_segments,
    fix_seams,
    interpolate_words,
    mark_suspect,
    normalize_chunk,
)
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


def test_normalize_adds_the_offset_once():
    raw = {"segments": [{"start": 1.0, "end": 2.0, "text": " Привет  мир "}]}
    out = normalize_chunk(raw, offset=600.0)
    assert out == [{"start": 601.0, "end": 602.0, "text": "Привет мир"}]


def test_normalize_keeps_the_quality_fields():
    """no_speech_prob и соседей несём дальше: по ним помечаются подозрительные сегменты."""
    raw = {"segments": [{"start": 0.0, "end": 1.0, "text": "а", "no_speech_prob": 0.7,
                         "avg_logprob": -0.3, "compression_ratio": 1.2}]}
    out = normalize_chunk(raw, offset=0.0)
    assert out[0]["no_speech_prob"] == 0.7 and out[0]["avg_logprob"] == -0.3


def test_normalize_drops_empty_and_inverted():
    raw = {"segments": [{"start": 5.0, "end": 4.0, "text": "назад"},
                        {"start": 1.0, "end": 2.0, "text": "  "}]}
    assert normalize_chunk(raw, offset=0.0) == []


def test_normalize_survives_a_response_without_segments():
    assert normalize_chunk({}, offset=0.0) == []
    assert normalize_chunk({"segments": None}, offset=0.0) == []


def test_seam_segment_is_trimmed_to_the_previous_end():
    """Фраза на границе приходит дважды: хвост в чанке N, голова в N+1."""
    segments = [{"start": 595.0, "end": 601.2, "text": "…"}, {"start": 600.0, "end": 604.0, "text": "…"}]
    fixed, stats = fix_seams(segments, boundaries=[600.0])
    assert fixed[1]["start"] == 601.2 and stats["fixed"] == 1


def test_seam_crumb_is_dropped():
    segments = [{"start": 595.0, "end": 601.2, "text": "…"}, {"start": 600.0, "end": 601.4, "text": "…"}]
    fixed, stats = fix_seams(segments, boundaries=[600.0])
    assert len(fixed) == 1 and stats["dropped"] == 1


def test_seam_far_from_the_boundary_is_left_alone():
    """Обычный сегмент, случайно перекрывший предыдущий, — не шов: его чинит не эта функция."""
    segments = [{"start": 100.0, "end": 110.0, "text": "…"}, {"start": 105.0, "end": 120.0, "text": "…"}]
    fixed, stats = fix_seams(segments, boundaries=[600.0])
    assert fixed[1]["start"] == 105.0 and stats == {"fixed": 0, "dropped": 0}


def test_clamp_cuts_the_tail_beyond_the_audio():
    """Whisper регулярно тянет последний сегмент за конец аудио — проверено живьём."""
    segments = [{"start": 770.0, "end": 783.9, "text": "…"}]
    out = clamp_segments(segments, duration=780.0)
    assert out[0]["end"] == 780.0


def test_clamp_drops_what_is_left_of_nothing():
    segments = [{"start": 781.0, "end": 783.9, "text": "…"}]
    assert clamp_segments(segments, duration=780.0) == []


def test_suspect_is_marked_not_deleted():
    """Помечаем, а не выбрасываем: решать человеку, а не порогу."""
    segments = [
        {"start": 0.0, "end": 1.0, "text": "…", "no_speech_prob": 0.9},
        {"start": 1.0, "end": 2.0, "text": "…", "avg_logprob": -1.5},
        {"start": 2.0, "end": 3.0, "text": "…", "compression_ratio": 3.0},
        {"start": 3.0, "end": 4.0, "text": "…", "no_speech_prob": 0.1},
    ]
    out = mark_suspect(segments)
    assert [s["suspect"] for s in out] == [True, True, True, False]


def test_words_are_syllable_weighted_and_flagged():
    """Слова провайдер не отдаёт: раскладываем по слогам и честно помечаем."""
    seg = {"start": 0.0, "end": 4.0, "text": "Привет большой мир"}
    words = interpolate_words(seg, silences=[])
    assert [w["w"] for w in words] == ["Привет", "большой", "мир"]
    assert all(w["interpolated"] for w in words)
    assert words[0]["s"] == 0.0 and abs(words[-1]["e"] - 4.0) < 1e-6
    assert (words[1]["e"] - words[1]["s"]) > (words[2]["e"] - words[2]["s"])


def test_words_are_back_to_back():
    seg = {"start": 10.0, "end": 14.0, "text": "раз два три"}
    words = interpolate_words(seg, silences=[])
    for before, after in pairwise(words):
        assert abs(before["e"] - after["s"]) < 1e-6


def test_words_skip_measured_silences():
    """Слово не должно «произноситься» в тишине: пауза внутри сегмента вырезается из раскладки."""
    seg = {"start": 0.0, "end": 6.0, "text": "раз два"}
    words = interpolate_words(seg, silences=[{"start": 2.0, "end": 4.0}])
    assert words[0]["e"] <= 2.0 + 1e-6 and words[1]["s"] >= 4.0 - 1e-6


def test_words_of_an_empty_text_are_empty():
    assert interpolate_words({"start": 0.0, "end": 1.0, "text": "   "}, silences=[]) == []
