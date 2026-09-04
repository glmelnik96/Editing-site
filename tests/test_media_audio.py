import math

import pytest

from server.app.config import Settings
from server.media.audio import (
    SILENCE_FLOOR_DB,
    db_to_amplitude,
    parse_levels,
    parse_silences,
    peaks_from_levels,
    silence_threshold_db,
    speech_level_db,
    wav_args,
)

ASTATS = """frame:0    pts:0       pts_time:0
lavfi.astats.Overall.Peak_level=-17.792199
frame:1    pts:320     pts_time:0.02
lavfi.astats.Overall.Peak_level=-inf
frame:2    pts:640     pts_time:0.04
lavfi.astats.Overall.Peak_level=0.000000
"""

SILENCE_LOG = """[silencedetect @ 0x1] silence_start: 3.018625
[silencedetect @ 0x1] silence_end: 6.014 | silence_duration: 2.995375
[silencedetect @ 0x1] silence_start: 9.5
"""


def test_parse_levels_handles_silence_and_full_scale():
    assert parse_levels(ASTATS) == [-17.792199, -math.inf, 0.0]
    assert parse_levels("мусор без чисел") == []


def test_db_to_amplitude():
    assert db_to_amplitude(0.0) == 255
    assert db_to_amplitude(-math.inf) == 0
    assert db_to_amplitude(-6.0) == pytest.approx(128, abs=2)
    assert db_to_amplitude(-100.0) == 0


def test_peaks_are_bytes_in_order():
    peaks = peaks_from_levels([0.0, -math.inf, -6.0])
    assert peaks == [255, 0, db_to_amplitude(-6.0)]
    assert all(0 <= p <= 255 for p in peaks)


def test_speech_level_is_median_of_the_loudest_windows():
    quiet = [-40.0] * 98
    loud = [-12.0, -10.0]
    assert speech_level_db(quiet + loud) == pytest.approx(-11.0, abs=0.5)
    assert speech_level_db([]) is None
    assert speech_level_db([-math.inf] * 10) is None


def test_threshold_follows_speech_and_has_a_floor():
    assert silence_threshold_db(-20.0, offset=16.0) == -36.0
    assert silence_threshold_db(-50.0, offset=16.0) == SILENCE_FLOOR_DB
    assert silence_threshold_db(None, offset=16.0) == -35.0  # запасной абсолютный порог


def test_parse_silences_pairs_starts_and_ends():
    assert parse_silences(SILENCE_LOG, duration=12.0) == [
        {"start": 3.019, "end": 6.014},
        {"start": 9.5, "end": 12.0},
    ]
    assert parse_silences("", duration=5.0) == []


def test_parse_silences_clamps_to_duration_and_drops_empty():
    log = "silence_start: 4.9\nsilence_end: 20.0 | silence_duration: 15\n"
    assert parse_silences(log, duration=5.0) == [{"start": 4.9, "end": 5.0}]
    assert parse_silences("silence_start: 5.0\n", duration=5.0) == []


def test_wav_args_are_16k_mono():
    args = wav_args(Settings(_env_file=None), "/x/source.mp4", "/x/audio16k.wav")
    assert "-ar" in args and "16000" in args
    assert "-ac" in args and "1" in args
    assert args[-1] == "/x/audio16k.wav"
    assert "-vn" in args


def test_parse_levels_handles_negative_nan():
    """Некоторые сборки ffmpeg печатают -nan: строка не должна выпадать из массива и сбивать индексы."""
    text = "lavfi.astats.Overall.RMS_level=-nan\nlavfi.astats.Overall.RMS_level=-12.5\n"
    levels = parse_levels(text)
    assert len(levels) == 2
    assert math.isnan(levels[0]) and levels[1] == -12.5
    assert peaks_from_levels(levels)[0] == 0
