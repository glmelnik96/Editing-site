import pytest

from server.app.config import Settings
from server.media.convert import (
    ConvertInvalid,
    ConvertUnavailable,
    build_convert_command,
    convert_ext,
)


def s(**over) -> Settings:
    return Settings(_env_file=None, **over)


def test_mp3_is_lame_stereo_at_192k_and_44100():
    args = build_convert_command(s(), "/x/source.mp4", "/x/out.part", fmt="mp3")
    assert args[0] == "ffmpeg"
    assert "-vn" in args
    assert args[args.index("-c:a") + 1] == "libmp3lame"
    assert args[args.index("-b:a") + 1] == "192k"
    assert args[args.index("-ar") + 1] == "44100"
    assert args[args.index("-ac") + 1] == "2"
    assert args[args.index("-f") + 1] == "mp3"
    assert args[-1] == "/x/out.part"
    assert "libx264" not in args
    assert "-progress" in args and args[args.index("-progress") + 1] == "pipe:1"


def test_m4a_is_aac_192k_without_video():
    args = build_convert_command(s(), "/x/source.mov", "/x/out.part", fmt="m4a")
    assert "-vn" in args
    assert args[args.index("-c:a") + 1] == "aac"
    assert args[args.index("-b:a") + 1] == "192k"
    # .part без расширения контейнера: ipod = m4a, как у прокси звука
    assert args[args.index("-f") + 1] == "ipod"
    assert "libmp3lame" not in args


def test_wav_is_pcm_16bit_48k_stereo():
    args = build_convert_command(s(), "/x/source.mp3", "/x/out.part", fmt="wav")
    assert "-vn" in args
    assert args[args.index("-c:a") + 1] == "pcm_s16le"
    assert args[args.index("-ar") + 1] == "48000"
    assert args[args.index("-ac") + 1] == "2"
    assert args[args.index("-f") + 1] == "wav"


def test_mp4_caps_short_side_at_1080_without_upscale():
    args = build_convert_command(s(), "/x/source.mp4", "/x/out.part", fmt="mp4")
    assert args[args.index("-c:v") + 1] == "libx264"
    assert args[args.index("-preset") + 1] == "veryfast"
    assert args[args.index("-crf") + 1] == "23"
    assert args[args.index("-c:a") + 1] == "aac"
    assert args[args.index("-b:a") + 1] == "160k"
    assert "+faststart" in args
    assert args[args.index("-f") + 1] == "mp4"
    scale = args[args.index("-vf") + 1]
    assert scale == "scale=w='if(gte(iw,ih),-2,min(iw,1080))':h='if(gte(iw,ih),min(ih,1080),-2)'"


def test_mp4_without_audio_drops_the_sound_track():
    args = build_convert_command(s(), "/x/source.mp4", "/x/out.part", fmt="mp4", has_audio=False)
    assert "-an" in args
    assert "-c:a" not in args


def test_unknown_format_is_invalid():
    with pytest.raises(ConvertInvalid) as exc:
        build_convert_command(s(), "/x/a.mp4", "/x/out.part", fmt="webm")
    assert exc.value.code == "invalid_format"


def test_missing_mp3_encoder_is_unavailable_not_raw_stderr():
    with pytest.raises(ConvertUnavailable) as exc:
        build_convert_command(s(), "/x/a.mp4", "/x/out.part", fmt="mp3", mp3_encoder=False)
    assert exc.value.code == "encoder_unavailable"
    assert "stderr" not in exc.value.message.lower()
    assert "libmp3lame" not in exc.value.message or "недоступ" in exc.value.message.lower()


def test_convert_ext_matches_whitelist():
    assert convert_ext("mp3") == "mp3"
    assert convert_ext("m4a") == "m4a"
    assert convert_ext("wav") == "wav"
    assert convert_ext("mp4") == "mp4"
    with pytest.raises(ConvertInvalid):
        convert_ext("gif")
