from server.app.config import Settings
from server.media.remux import can_remux, remux_args


def test_only_the_matroska_family_is_repacked():
    """ffprobe называет и .webm, и .mkv «matroska,webm»; остальным переупаковка не поможет."""
    assert can_remux("matroska,webm")
    assert not can_remux("mov,mp4,m4a,3gp,3g2,mj2")
    assert not can_remux("")


def test_remux_copies_every_track_into_matroska():
    """Копия без перекодирования: все дорожки как есть, контейнер Matroska — он примет и H.264,
    который браузер тоже умеет писать в .webm."""
    args = remux_args(Settings(_env_file=None), "/x/source.webm", "/x/source.webm.part")
    assert args[0] == "ffmpeg" and args[-1] == "/x/source.webm.part"
    assert args[args.index("-i") + 1] == "/x/source.webm"
    assert args[args.index("-map") + 1] == "0" and args[args.index("-c") + 1] == "copy"
    assert args[args.index("-f") + 1] == "matroska"
