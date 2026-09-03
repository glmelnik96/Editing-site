from server.app.ratelimit import FixedWindowLimiter


def test_limiter_allows_up_to_max_then_blocks_until_window_passes():
    clock = {"t": 100.0}
    limiter = FixedWindowLimiter(max_hits=2, window_sec=60, clock=lambda: clock["t"])
    assert limiter.allow("ip1") is True
    assert limiter.allow("ip1") is True
    assert limiter.allow("ip1") is False
    assert limiter.allow("ip2") is True
    clock["t"] += 61
    assert limiter.allow("ip1") is True
