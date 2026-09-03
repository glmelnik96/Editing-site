from pathlib import Path

import pytest

from tools.bench_ffmpeg import bench_commands, realtime_factor, render_report, sample_command


def test_realtime_factor():
    assert realtime_factor(60, 30) == 2.0
    assert realtime_factor(60, 90) == 0.67
    with pytest.raises(ValueError):
        realtime_factor(60, 0)


def test_bench_commands_match_spec_presets(tmp_path):
    cmds = bench_commands(Path("sample.mp4"), tmp_path)
    assert set(cmds) == {"proxy", "draft", "final"}
    proxy, draft, final = cmds["proxy"], cmds["draft"], cmds["final"]
    assert proxy[0] == "ffmpeg" and "-i" in proxy and "sample.mp4" in proxy
    assert proxy[proxy.index("-preset") + 1] == "veryfast" and proxy[proxy.index("-crf") + 1] == "28"
    assert "-g" in proxy and proxy[proxy.index("-g") + 1] == "30"
    assert draft[draft.index("-preset") + 1] == "ultrafast" and draft[draft.index("-crf") + 1] == "26"
    assert draft[draft.index("-vf") + 1] == "scale=-2:720"
    assert final[final.index("-preset") + 1] == "veryfast" and final[final.index("-crf") + 1] == "20"
    assert final[final.index("-vf") + 1] == "scale=-2:1080"
    assert final[-1] == str(tmp_path / "final.mp4")


def test_sample_command_is_4k_testsrc(tmp_path):
    cmd = sample_command(tmp_path / "s.mp4", seconds=60)
    assert "testsrc2=size=3840x2160:rate=30" in cmd and cmd[cmd.index("-t") + 1] == "60"


def test_render_report_has_table_rows():
    text = render_report("vm-1", 4, 60.0, {"proxy": 20.0, "draft": 10.0, "final": 40.0})
    assert "| proxy | 20.0 | 3.0× |" in text
    assert "| final | 40.0 | 1.5× |" in text
    assert "vm-1" in text and "4 потоков" in text
