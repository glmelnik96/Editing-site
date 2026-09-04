import sys

import pytest

from server.app.config import Settings
from server.media.probe import MediaInfo, parse_probe, probe_args
from server.media.run import MediaError, run_tool, tail_lines

VIDEO_JSON = {
    "format": {"duration": "12.000000", "size": "2789362"},
    "streams": [
        {
            "codec_type": "video", "codec_name": "h264", "width": 640, "height": 360,
            "avg_frame_rate": "25/1", "r_frame_rate": "25/1", "duration": "12.0",
        },
        {"codec_type": "audio", "codec_name": "aac", "channels": 1, "duration": "12.0"},
    ],
}


def test_parse_video_with_sound():
    info = parse_probe(VIDEO_JSON)
    assert info == MediaInfo(
        duration=12.0, width=640, height=360, fps=25.0, has_audio=True,
        video_codec="h264", audio_codec="aac",
    )


def test_parse_audio_only():
    info = parse_probe({"format": {"duration": "3.5"}, "streams": [
        {"codec_type": "audio", "codec_name": "mp3", "channels": 2},
    ]})
    assert info.width is None and info.height is None and info.fps is None
    assert info.has_audio is True and info.video_codec is None and info.audio_codec == "mp3"
    assert info.duration == 3.5


def test_parse_video_without_sound():
    info = parse_probe({"format": {"duration": "1"}, "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 2, "height": 2, "avg_frame_rate": "0/0"},
    ]})
    assert info.has_audio is False and info.fps is None


def test_duration_falls_back_to_stream():
    info = parse_probe({"format": {}, "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 2, "height": 2,
         "avg_frame_rate": "30000/1001", "duration": "4.25"},
    ]})
    assert info.duration == 4.25
    assert info.fps == pytest.approx(29.97, abs=0.01)


def test_cover_art_is_not_video():
    """Обложка mp3 приходит видеопотоком: считаем такой файл звуком, а не видео."""
    info = parse_probe({"format": {"duration": "10"}, "streams": [
        {"codec_type": "video", "codec_name": "mjpeg", "width": 300, "height": 300,
         "avg_frame_rate": "0/0", "disposition": {"attached_pic": 1}},
        {"codec_type": "audio", "codec_name": "mp3", "channels": 2},
    ]})
    assert info.width is None and info.video_codec is None and info.has_audio is True


def test_broken_file_raises():
    with pytest.raises(MediaError) as e:
        parse_probe({"format": {"duration": "0"}, "streams": []})
    assert e.value.reason == "no_streams"
    with pytest.raises(MediaError):
        parse_probe({"format": {"duration": "nonsense"}, "streams": [
            {"codec_type": "audio", "codec_name": "mp3"},
        ]})


def test_probe_args_asks_for_json_only():
    args = probe_args(Settings(_env_file=None), "/x/source.mp4")
    assert args[0] == "ffprobe"
    assert "-print_format" in args and "json" in args
    assert args[-1] == "/x/source.mp4"


def test_tail_lines_keeps_the_end():
    assert tail_lines("a\nb\nc\nd", 2) == "c\nd"
    assert tail_lines("", 5) == ""
    assert tail_lines("одна строка", 5) == "одна строка"


def test_run_tool_reports_exit_code(tmp_path):
    # -X utf8: без этого флага дочерний интерпретатор на Windows при перенаправленном stderr
    # берёт кодировку консоли (не UTF-8), и кириллица приходит битой независимо от run_tool.
    with pytest.raises(MediaError) as e:
        run_tool(
            [sys.executable, "-X", "utf8", "-c", "import sys; sys.stderr.write('плохо\\n'); sys.exit(3)"],
            timeout=30,
        )
    assert e.value.reason == "tool_failed"
    assert "плохо" in e.value.stderr
    out = run_tool([sys.executable, "-X", "utf8", "-c", "print('привет')"], timeout=30)
    assert out.strip() == "привет"


def test_run_tool_timeout():
    with pytest.raises(MediaError) as e:
        run_tool([sys.executable, "-X", "utf8", "-c", "import time; time.sleep(5)"], timeout=0.3)
    assert e.value.reason == "timeout"
