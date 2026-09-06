from server.media.verify import verify_segment_boundaries


def test_boundary_inside_a_pause_is_exact():
    """Граница внутри паузы — точный ответ без ограничения окна: речь возобновляется в её конце,
    а закончилась в её начале."""
    segments = [{"start": 5.0, "end": 9.0, "text": "…"}]
    silences = [{"start": 4.0, "end": 6.0}, {"start": 8.5, "end": 10.0}]
    out, _ = verify_segment_boundaries(segments, silences)
    assert out[0]["start"] == 6.0 and out[0]["start_verified"] is True
    assert out[0]["end"] == 8.5 and out[0]["end_verified"] is True


def test_expands_up_to_a_second_but_squeezes_only_a_quarter():
    """Окна несимметричны: расширять речь можно щедро, сжимать — скупо."""
    segments = [{"start": 10.0, "end": 20.0, "text": "…"}]
    silences = [{"start": 9.0, "end": 9.2}, {"start": 20.9, "end": 21.5}]
    out, _ = verify_segment_boundaries(segments, silences)
    assert out[0]["start"] == 9.2 and out[0]["end"] == 20.9


def test_far_pause_does_not_move_the_boundary():
    segments = [{"start": 10.0, "end": 20.0, "text": "…"}]
    silences = [{"start": 5.0, "end": 5.5}, {"start": 30.0, "end": 31.0}]
    out, _ = verify_segment_boundaries(segments, silences)
    assert out[0]["start"] == 10.0 and out[0]["end"] == 20.0


def test_unverifiable_boundary_is_left_alone():
    """«Проверить нечем» честнее, чем двигать вслепую."""
    segments = [{"start": 10.0, "end": 20.0, "text": "…"}]
    out, stats = verify_segment_boundaries(segments, [{"start": 100.0, "end": 101.0}])
    assert out[0]["start"] == 10.0 and out[0]["start_verified"] is False
    assert out[0]["end_verified"] is False and stats["verified_pct"] == 0


def test_snap_does_not_cross_into_the_next_segment():
    segments = [{"start": 0.0, "end": 5.0, "text": "…"}, {"start": 5.4, "end": 9.0, "text": "…"}]
    out, _ = verify_segment_boundaries(segments, [{"start": 5.6, "end": 6.0}])
    assert out[0]["end"] <= 5.4


def test_start_does_not_climb_over_the_previous_end():
    segments = [{"start": 0.0, "end": 5.0, "text": "…"}, {"start": 5.2, "end": 9.0, "text": "…"}]
    out, _ = verify_segment_boundaries(segments, [{"start": 4.0, "end": 4.5}])
    assert out[1]["start"] >= out[0]["end"]


def test_zero_start_is_not_touched():
    """Запись начинается с комнатного тона: первая пауза не значит, что речь позже."""
    segments = [{"start": 0.0, "end": 5.0, "text": "…"}]
    out, _ = verify_segment_boundaries(segments, [{"start": 0.0, "end": 0.5}])
    assert out[0]["start"] == 0.0


def test_segment_never_becomes_shorter_than_a_blink():
    segments = [{"start": 5.0, "end": 5.1, "text": "…"}]
    out, _ = verify_segment_boundaries(segments, [{"start": 4.0, "end": 5.09}])
    assert out[0]["end"] - out[0]["start"] >= 0.05


def test_other_fields_survive():
    segments = [{"start": 5.0, "end": 9.0, "text": "привет", "suspect": True, "id": 3}]
    out, _ = verify_segment_boundaries(segments, [{"start": 4.0, "end": 6.0}])
    assert out[0]["text"] == "привет" and out[0]["suspect"] is True and out[0]["id"] == 3


def test_stats_report_the_work_done():
    segments = [{"start": 5.0, "end": 9.0, "text": "…"}]
    silences = [{"start": 4.0, "end": 6.0}, {"start": 8.5, "end": 10.0}]
    _, stats = verify_segment_boundaries(segments, silences)
    assert stats["verified_pct"] == 100 and stats["adjusted"] == 1
    assert stats["max_drift"] > 0 and stats["total"] == 1


def test_without_a_silence_map_nothing_is_verified():
    """Карты нет — не выдумываем: все флаги false, ни одна граница не сдвинута."""
    segments = [{"start": 5.0, "end": 9.0, "text": "…"}]
    out, stats = verify_segment_boundaries(segments, [])
    assert out[0]["start"] == 5.0 and out[0]["end"] == 9.0
    assert out[0]["start_verified"] is False and stats["verified_pct"] == 0


def test_unsorted_silences_are_handled():
    segments = [{"start": 5.0, "end": 9.0, "text": "…"}]
    silences = [{"start": 8.5, "end": 10.0}, {"start": 4.0, "end": 6.0}]
    out, _ = verify_segment_boundaries(segments, silences)
    assert out[0]["start"] == 6.0 and out[0]["end"] == 8.5


def test_input_is_not_mutated():
    """Транскрипт провайдера остаётся нетронутым: правки видны только в копиях."""
    segments = [{"start": 5.0, "end": 9.0, "text": "…"}]
    verify_segment_boundaries(segments, [{"start": 4.0, "end": 6.0}])
    assert segments == [{"start": 5.0, "end": 9.0, "text": "…"}]


def test_empty_transcript_gives_empty_stats():
    """Пустой список сегментов: доля подтверждённых — ноль, а не деление на ноль."""
    out, stats = verify_segment_boundaries([], [{"start": 4.0, "end": 6.0}])
    assert out == [] and stats["total"] == 0 and stats["verified_pct"] == 0


def test_degenerate_segment_keeps_the_shape():
    """Сегмент нулевой длины двигать некуда, но флаги стоят: форма записи одна для всех."""
    out, stats = verify_segment_boundaries([{"start": 5.0, "end": 5.0}], [{"start": 4.0, "end": 6.0}])
    assert out[0]["start"] == 5.0 and out[0]["end"] == 5.0
    assert out[0]["start_verified"] is False and out[0]["end_verified"] is False
    assert stats["adjusted"] == 0
