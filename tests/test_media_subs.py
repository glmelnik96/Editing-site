"""SRT и VTT из реплик. Чистые функции: ни диска, ни сети, ни ffmpeg."""
from server.media.cues import build_cues
from server.media.subs import cues_to_srt, cues_to_vtt, to_srt, to_vtt


def transcript(*segments: dict) -> dict:
    """Транскрипт из голых сегментов: экспорту нужны только start, end и text."""
    return {"asset_id": "ast_000000000001", "duration": 60.0, "segments": list(segments)}


def segment(start: float, end: float, text: str) -> dict:
    return {"start": start, "end": end, "text": text}


def test_srt_numbers_replicas_from_one():
    out = to_srt(transcript(segment(0.0, 1.0, "раз"), segment(2.0, 3.0, "два")))
    assert out.splitlines()[0] == "1"
    assert out.splitlines()[4] == "2"


def test_srt_time_uses_a_comma():
    out = to_srt(transcript(segment(1.23, 4.913, "привет")))
    assert out.splitlines()[1] == "00:00:01,230 --> 00:00:04,913"


def test_vtt_time_uses_a_dot():
    out = to_vtt(transcript(segment(1.23, 4.913, "привет")))
    assert "00:00:01.230 --> 00:00:04.913" in out


def test_vtt_starts_with_the_header_and_a_blank_line():
    out = to_vtt(transcript(segment(0.0, 1.0, "раз")))
    assert out.startswith("WEBVTT\n\n")
    assert out.splitlines()[2] == "00:00:00.000 --> 00:00:01.000"


def test_blank_line_between_replicas_and_a_trailing_newline():
    out = to_srt(transcript(segment(0.0, 1.0, "раз"), segment(2.0, 3.0, "два")))
    assert out == "1\n00:00:00,000 --> 00:00:01,000\nраз\n\n2\n00:00:02,000 --> 00:00:03,000\nдва\n"
    vtt = to_vtt(transcript(segment(0.0, 1.0, "раз"), segment(2.0, 3.0, "два")))
    assert vtt.endswith("два\n") and "\n\n\n" not in vtt


def test_empty_transcript_gives_an_empty_file():
    assert to_srt(transcript()) == ""
    # У VTT остаётся заголовок: файл без него плеер не примет вовсе.
    assert to_vtt(transcript()) == "WEBVTT\n\n"


def test_hours_are_formatted():
    out = to_srt(transcript(segment(3723.4, 7384.02, "долго")))
    assert out.splitlines()[1] == "01:02:03,400 --> 02:03:04,020"


def test_newline_inside_text_is_a_second_line():
    out = to_srt(transcript(segment(0.0, 1.0, "первая\nвторая")))
    assert out == "1\n00:00:00,000 --> 00:00:01,000\nпервая\nвторая\n"


def test_blank_lines_inside_text_are_collapsed():
    """Пустая строка внутри реплики кончает её досрочно: остаток текста стал бы новой репликой."""
    out = to_srt(transcript(segment(0.0, 1.0, "первая\n\n\n  \nвторая"), segment(2.0, 3.0, "хвост")))
    assert out.startswith("1\n00:00:00,000 --> 00:00:01,000\nпервая\nвторая\n\n2\n")


def test_segments_are_sorted_by_time():
    out = to_srt(transcript(segment(5.0, 6.0, "поздняя"), segment(1.0, 2.0, "ранняя")))
    assert out.splitlines()[2] == "ранняя"
    assert out.splitlines()[6] == "поздняя"


def test_segments_without_text_or_time_are_skipped():
    """Нумерация идёт по выданным репликам: пропуск не должен оставлять дыру в номерах."""
    out = to_srt(transcript(
        segment(0.0, 1.0, "  "),
        {"start": None, "end": 2.0, "text": "без начала"},
        segment(3.0, 4.0, "единственная"),
    ))
    assert out == "1\n00:00:03,000 --> 00:00:04,000\nединственная\n"


def test_negative_time_is_clamped_to_zero():
    out = to_srt(transcript(segment(-0.5, 1.0, "раньше файла")))
    assert out.splitlines()[1] == "00:00:00,000 --> 00:00:01,000"


def test_cues_from_the_typography_module_need_no_conversion():
    """Реплика из cues.py — тот же {start, end, text}, что и сегмент: приводить одно к другому
    нечего, экспорт просто получает список без обёртки транскрипта."""
    words = [{"w": "Мы", "s": 0.0, "e": 0.4}, {"w": "поехали", "s": 0.4, "e": 1.0},
             {"w": "в", "s": 1.0, "e": 1.1}, {"w": "большой", "s": 1.1, "e": 1.8},
             {"w": "старый", "s": 1.8, "e": 2.4}, {"w": "дом", "s": 2.4, "e": 2.9}]
    cues = build_cues(words, max_chars=20, max_lines=2, max_dur=4.0)
    out = cues_to_srt(cues)
    assert out.startswith("1\n00:00:00,000 --> 00:00:02,900\n")
    # Перенос, который выбрала типографика, доезжает до файла второй строкой реплики.
    assert out.splitlines()[2:4] == ["Мы поехали", "в большой старый дом"]
    assert cues_to_vtt(cues).startswith("WEBVTT\n\n00:00:00.000 --> 00:00:02.900\n")


def test_the_transcript_export_is_the_same_formatter():
    """Транскрипт — те же реплики под ключом segments, и обёртка не должна менять вывод."""
    cue = {"start": 1.23, "end": 4.913, "text": "привет"}
    assert to_srt({"segments": [cue]}) == cues_to_srt([cue])
    assert to_vtt({"segments": [cue]}) == cues_to_vtt([cue])
    assert to_srt({}) == cues_to_srt([]) and to_srt("не словарь") == ""


def test_words_and_extra_fields_do_not_leak_into_subtitles():
    """В субтитрах только текст: пословные времена нужны панели и агенту, а не плееру."""
    rich = {
        "id": 1, "start": 0.0, "end": 1.0, "text": "привет", "suspect": True,
        "words": [{"w": "привет", "s": 0.0, "e": 1.0, "interpolated": True}],
    }
    assert to_srt(transcript(rich)) == "1\n00:00:00,000 --> 00:00:01,000\nпривет\n"
