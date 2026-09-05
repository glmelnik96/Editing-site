import pytest

from server.media.subtitles import SubtitleInvalid, to_vtt

SRT = """1
00:00:01,000 --> 00:00:03,500
Привет, мир

2
00:00:04,000 --> 00:00:06,000
Вторая реплика
и её вторая строка
"""

VTT = """WEBVTT

00:00:01.000 --> 00:00:03.500
Привет
"""


def test_srt_becomes_vtt():
    out = to_vtt(SRT, ext="srt")
    assert out.startswith("WEBVTT\n\n")
    assert "00:00:01.000 --> 00:00:03.500" in out
    assert "00:00:04.000 --> 00:00:06.000" in out
    assert "Привет, мир" in out and "и её вторая строка" in out
    assert "-->" in out and "," not in out.split("\n")[2]


def test_srt_numbering_is_dropped():
    out = to_vtt(SRT, ext="srt")
    assert not any(line.strip() == "1" for line in out.splitlines())


def test_vtt_passes_through():
    assert to_vtt(VTT, ext="vtt") == VTT


def test_vtt_without_a_header_is_rejected():
    with pytest.raises(SubtitleInvalid):
        to_vtt("00:00:01.000 --> 00:00:02.000\nтекст\n", ext="vtt")


def test_hours_and_short_forms():
    out = to_vtt("1\n01:02:03,004 --> 01:02:04,000\nтекст\n", ext="srt")
    assert "01:02:03.004 --> 01:02:04.000" in out


def test_empty_or_broken_srt_is_rejected():
    with pytest.raises(SubtitleInvalid):
        to_vtt("", ext="srt")
    with pytest.raises(SubtitleInvalid):
        to_vtt("совсем не субтитры", ext="srt")


def test_byte_order_mark_and_crlf_are_handled():
    out = to_vtt("﻿1\r\n00:00:01,000 --> 00:00:02,000\r\nтекст\r\n", ext="srt")
    assert out.startswith("WEBVTT")
    assert "\r" not in out
    assert "текст" in out


def test_unknown_extension_is_rejected():
    with pytest.raises(SubtitleInvalid):
        to_vtt(SRT, ext="txt")
