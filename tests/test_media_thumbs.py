from server.app.config import Settings
from server.media.thumbs import GridLayout, grid_layout, thumbs_args, thumbs_meta


def s(**over) -> Settings:
    return Settings(_env_file=None, **over)


def test_layout_one_frame_per_interval():
    layout = grid_layout(s(), duration=12.0, width=640, height=360)
    assert layout == GridLayout(count=6, cols=10, rows=1, interval=2.0, frame_width=160, frame_height=90)


def test_layout_rounds_rows_up_and_caps_frames():
    layout = grid_layout(s(), duration=3600.0, width=1920, height=1080)
    assert layout.count == 600 and layout.cols == 10 and layout.rows == 60
    assert layout.interval == 6.0  # 3600 / 600: интервал растянут, чтобы уложиться в предел
    assert layout.frame_width == 160 and layout.frame_height == 90


def test_layout_never_empty_and_keeps_even_height():
    layout = grid_layout(s(), duration=0.4, width=101, height=57)
    assert layout.count == 1 and layout.rows == 1
    assert layout.frame_height % 2 == 0


def test_layout_for_vertical_video():
    layout = grid_layout(s(), duration=10.0, width=1080, height=1920)
    assert layout.frame_width == 160 and layout.frame_height == 284


def test_args_use_fps_and_tile():
    layout = grid_layout(s(), duration=12.0, width=640, height=360)
    args = thumbs_args(s(), "/x/source.mp4", "/x/thumbs.jpg", layout)
    chain = args[args.index("-vf") + 1]
    assert chain == "fps=1/2.0,scale=160:-2,tile=10x1"
    assert "-frames:v" in args and args[args.index("-frames:v") + 1] == "1"
    assert args[-1] == "/x/thumbs.jpg"


def test_meta_describes_the_sprite():
    layout = grid_layout(s(), duration=12.0, width=640, height=360)
    assert thumbs_meta(layout) == {
        "count": 6, "cols": 10, "rows": 1, "interval": 2.0, "width": 160, "height": 90,
    }
