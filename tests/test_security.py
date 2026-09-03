from server.app.security import is_cross_site


def test_origin_header_decides_when_present():
    assert is_cross_site({"origin": "https://evil.example"}, "https://video.example.ru") is True
    assert is_cross_site({"origin": "https://video.example.ru"}, "https://video.example.ru") is False
    assert is_cross_site({"origin": "https://VIDEO.example.ru/"}, "https://video.example.ru") is False


def test_sec_fetch_site_used_without_origin():
    assert is_cross_site({"sec-fetch-site": "cross-site"}, "https://video.example.ru") is True
    assert is_cross_site({"sec-fetch-site": "same-origin"}, "https://video.example.ru") is False


def test_no_headers_means_not_cross_site():
    assert is_cross_site({}, "https://video.example.ru") is False
