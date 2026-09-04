import pytest

from server.app.config import Settings
from server.media.proxy import parse_progress, proxy_args, proxy_name


def s(**over) -> Settings:
    return Settings(_env_file=None, **over)


def test_video_proxy_is_h264_with_short_gop():
    args = proxy_args(s(), "/x/source.mp4", "/x/proxy.mp4", kind="video")
    assert "libx264" in args and "veryfast" in args
    assert args[args.index("-crf") + 1] == "28"
    assert args[args.index("-g") + 1] == "30"
    assert "-sc_threshold" in args and args[args.index("-sc_threshold") + 1] == "0"
    assert args[args.index("-b:a") + 1] == "96k"
    assert "+faststart" in args
    # кодирование идёт во временный файл proxy.mp4.part — по расширению .part ffmpeg не
    # угадывает контейнер сам, поэтому формат передаётся явно
    assert args[args.index("-f") + 1] == "mp4"
    scale = args[args.index("-vf") + 1]
    # min() не даёт увеличивать кадр меньше 640 px
    assert scale == "scale=w='if(gte(iw,ih),min(iw,640),-2)':h='if(gte(iw,ih),-2,min(ih,640))'"
    assert "-progress" in args and args[args.index("-progress") + 1] == "pipe:1"


def test_audio_proxy_has_no_video():
    args = proxy_args(s(), "/x/source.mp3", "/x/proxy.m4a", kind="audio")
    assert "-vn" in args and "libx264" not in args
    assert args[args.index("-b:a") + 1] == "96k"
    # тот же .part-файл для m4a: контейнер ipod (m4a) тоже нужно называть явно
    assert args[args.index("-f") + 1] == "ipod"


def test_long_side_is_configurable():
    args = proxy_args(s(proxy_long_side=480), "/x/a.mp4", "/x/p.mp4", kind="video")
    assert "480" in args[args.index("-vf") + 1]


def test_proxy_name_by_kind():
    assert proxy_name("video") == "proxy.mp4"
    assert proxy_name("audio") == "proxy.m4a"
    with pytest.raises(ValueError):
        proxy_name("subtitle")


def test_progress_lines():
    assert parse_progress("out_time_us=1500000", total=3.0) == pytest.approx(0.5)
    assert parse_progress("out_time_us=9000000", total=3.0) == 1.0
    assert parse_progress("out_time_us=N/A", total=3.0) is None
    assert parse_progress("frame=12", total=3.0) is None
    assert parse_progress("out_time_us=100", total=0.0) is None
