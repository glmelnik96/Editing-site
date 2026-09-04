import sys
import time

import pytest

from server.media.run import MediaError, run_streaming

COUNTER = "import sys, time\nfor i in range(50):\n    print(i, flush=True)\n    time.sleep(0.05)\n"
NOISY_FAIL = "import sys\nsys.stderr.write('плохой кодек\\n')\nsys.exit(2)\n"


def test_lines_are_streamed_in_order():
    seen = []
    run_streaming([sys.executable, "-c", "print('a'); print('b')"], timeout=30, on_line=seen.append)
    assert [s.strip() for s in seen if s.strip()] == ["a", "b"]


def test_stop_check_terminates_the_process():
    started = time.monotonic()
    with pytest.raises(MediaError) as e:
        run_streaming(
            [sys.executable, "-c", COUNTER],
            timeout=60,
            on_line=lambda _l: None,
            should_stop=lambda: True,
            stop_check_sec=0.05,
        )
    assert e.value.reason == "canceled"
    assert time.monotonic() - started < 15  # не ждём полного прогона в 2.5 с × запас


def test_failure_carries_stderr():
    with pytest.raises(MediaError) as e:
        run_streaming([sys.executable, "-X", "utf8", "-c", NOISY_FAIL], timeout=30, on_line=lambda _l: None)
    assert e.value.reason == "tool_failed" and "плохой кодек" in e.value.stderr


def test_timeout_kills_the_process():
    with pytest.raises(MediaError) as e:
        run_streaming([sys.executable, "-c", COUNTER], timeout=0.2, on_line=lambda _l: None)
    assert e.value.reason == "timeout"


def test_missing_tool():
    with pytest.raises(MediaError) as e:
        run_streaming(["ffmpeg-которого-нет"], timeout=5, on_line=lambda _l: None)
    assert e.value.reason == "tool_missing"
