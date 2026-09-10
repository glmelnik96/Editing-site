import json
import sys

import pytest

from server.app.config import Settings
from server.media.probe import MediaInfo, parse_probe, probe_args, probe_file
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


def test_run_tool_reports_exit_code():
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


def test_empty_tool_path_falls_back_to_path_lookup(monkeypatch):
    monkeypatch.setenv("VIDEO_FFMPEG_PATH", "")
    monkeypatch.setenv("VIDEO_FFPROBE_PATH", "   ")
    s = Settings(_env_file=None)
    assert s.ffmpeg_path == "ffmpeg" and s.ffprobe_path == "ffprobe"


def test_probe_file_parses_tool_output(monkeypatch):
    monkeypatch.setattr("server.media.probe.run_tool", lambda *a, **k: json.dumps(VIDEO_JSON))
    info = probe_file(Settings(_env_file=None), "/x/source.mp4")
    assert info.duration == 12.0 and info.kind == "video"


def test_probe_file_rejects_non_json(monkeypatch):
    monkeypatch.setattr("server.media.probe.run_tool", lambda *a, **k: "не json")
    with pytest.raises(MediaError) as e:
        probe_file(Settings(_env_file=None), "/x/source.mp4")
    assert e.value.reason == "bad_probe"


# Ответы ffprobe сняты с настоящих файлов (ffmpeg 7.x), а не придуманы: у JPEG длительность
# приходит правдоподобной, у PNG её нет вовсе, а gif бывает и картинкой, и анимацией.
JPEG_JSON = {
    "format": {"format_name": "image2", "duration": "0.040000", "bit_rate": "6054600"},
    "streams": [{
        "codec_type": "video", "codec_name": "mjpeg", "width": 800, "height": 600,
        "avg_frame_rate": "25/1", "duration": "0.040000",
    }],
}
PNG_JSON = {
    "format": {"format_name": "png_pipe"},
    "streams": [{
        "codec_type": "video", "codec_name": "png", "width": 1280, "height": 720,
        "avg_frame_rate": "25/1",
    }],
}


def test_still_image_has_no_duration_and_no_bitrate():
    """У картинки нет длительности: сколько она висит в кадре, решают на шкале.

    Битрейта у неё тоже нет по смыслу — «6 Мбит/с» у JPEG это его вес, делённый на выдуманные
    ffprobe 0.04 секунды. Записать такое в assets значило бы потом предложить это число как
    «качество исходника» в рендере.
    """
    for data in (JPEG_JSON, PNG_JSON):
        info = parse_probe(data)
        assert info.kind == "image"
        assert info.still is True
        assert info.duration is None
        assert info.bit_rate is None
        assert info.fps is None
        assert info.has_audio is False
    assert parse_probe(JPEG_JSON).width == 800
    assert parse_probe(PNG_JSON).height == 720


def test_animated_gif_stays_a_video_but_a_single_frame_gif_is_a_picture():
    """gif — единственный контейнер картинок, который бывает анимацией. Различает число кадров."""
    def gif(frames: str, duration: str) -> dict:
        return {
            "format": {"format_name": "gif", "duration": duration},
            "streams": [{
                "codec_type": "video", "codec_name": "gif", "width": 160, "height": 120,
                "avg_frame_rate": "10/1", "duration": duration, "nb_frames": frames,
            }],
        }

    moving = parse_probe(gif("22", "2.200000"))
    assert moving.kind == "video"
    assert moving.duration == 2.2

    frozen = parse_probe(gif("1", "0.040000"))
    assert frozen.kind == "image"
    assert frozen.duration is None


def test_video_keeps_its_bitrate_and_prefers_the_stream_over_the_container():
    """Битрейт видеопотока точнее общего: в общий входят звук и обвязка контейнера."""
    both = parse_probe({
        "format": {"format_name": "mov,mp4", "duration": "10", "bit_rate": "9000000"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
             "avg_frame_rate": "30/1", "bit_rate": "8000000"},
            {"codec_type": "audio", "codec_name": "aac", "bit_rate": "160000"},
        ],
    })
    assert both.bit_rate == 8_000_000
    assert both.kind == "video"

    # В mkv и webm битрейта потока обычно нет — тогда берём общий по файлу.
    container_only = parse_probe({
        "format": {"format_name": "matroska,webm", "duration": "10", "bit_rate": "5000000"},
        "streams": [{"codec_type": "video", "codec_name": "vp9", "width": 1280, "height": 720,
                     "avg_frame_rate": "25/1"}],
    })
    assert container_only.bit_rate == 5_000_000

    nothing = parse_probe({
        "format": {"format_name": "matroska", "duration": "10"},
        "streams": [{"codec_type": "video", "codec_name": "vp9", "width": 1280, "height": 720,
                     "avg_frame_rate": "25/1"}],
    })
    assert nothing.bit_rate is None


def test_a_picture_with_sound_is_not_a_picture():
    """Обложка звукового файла приходит видеопотоком, и контейнер у неё бывает image2."""
    info = parse_probe({
        "format": {"format_name": "image2", "duration": "180"},
        "streams": [
            {"codec_type": "video", "codec_name": "mjpeg", "width": 500, "height": 500,
             "avg_frame_rate": "25/1"},
            {"codec_type": "audio", "codec_name": "mp3", "duration": "180"},
        ],
    })
    assert info.kind != "image"
    assert info.duration == 180.0
