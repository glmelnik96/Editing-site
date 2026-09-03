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


def test_limiter_bounds_number_of_keys():
    clock = {"t": 0.0}
    limiter = FixedWindowLimiter(max_hits=5, window_sec=60, clock=lambda: clock["t"], max_keys=3)
    for key in ("a", "b", "c"):
        assert limiter.allow(key) is True
    assert limiter.tracked_keys == 3
    clock["t"] += 61
    assert limiter.allow("d") is True
    assert limiter.tracked_keys == 1
    for key in ("e", "f"):
        assert limiter.allow(key) is True
    assert limiter.allow("g") is True
    assert limiter.tracked_keys == 1
    assert limiter.allow("a") is True
