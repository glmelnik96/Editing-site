import pytest

from server.app.config import Settings
from server.app.storage import (
    PUBLIC_FILES,
    asset_dir,
    file_url,
    kind_from_ext,
    parse_file_url,
    render_dir,
    render_url,
    safe_ext,
    upload_path,
)


def test_safe_ext_lowercases_and_rejects_garbage():
    assert safe_ext("Clip.MP4") == "mp4"
    assert safe_ext("noext") == "bin"
    assert safe_ext("weird.tar.gz") == "gz"
    assert safe_ext("bad.ext with space") == "bin"
    assert safe_ext("x." + "a" * 9) == "bin"
    assert safe_ext(".mp4") == "mp4"
    assert safe_ext("a.") == "bin"
    assert safe_ext("клип.мп4") == "bin"


def test_kind_from_ext():
    assert kind_from_ext("mov") == "video"
    assert kind_from_ext("mp3") == "audio"
    assert kind_from_ext("srt") == "subtitle"
    assert kind_from_ext("bin") is None
    assert kind_from_ext("MP4") == "video"


def test_paths_come_from_ids_only(tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path / "d")
    assert asset_dir(s, "usr_0123456789ab", "ast_0123456789ab") == (
        tmp_path / "d" / "usr_0123456789ab" / "assets" / "ast_0123456789ab"
    )
    assert upload_path(s, "upl_0123456789ab") == tmp_path / "d" / "tmp" / "uploads" / "upl_0123456789ab"
    for bad in ("u", "../../etc", "usr_0123456789ab/x", "USR_0123456789AB"):
        with pytest.raises(ValueError):
            asset_dir(s, bad, "ast_0123456789ab")
    with pytest.raises(ValueError):
        upload_path(s, "../x")


def test_render_paths_come_from_ids(tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path / "d")
    assert render_dir(s, "usr_0123456789ab", "prj_0123456789ab") == (
        tmp_path / "d" / "usr_0123456789ab" / "projects" / "prj_0123456789ab" / "renders"
    )
    assert render_url("usr_0123456789ab", "prj_0123456789ab", "rnd_0123456789ab") == (
        "/files/usr_0123456789ab/projects/prj_0123456789ab/renders/rnd_0123456789ab.mp4"
    )
    with pytest.raises(ValueError):
        render_dir(s, "../../etc", "prj_0123456789ab")


def test_file_url_roundtrip():
    url = file_url("usr_0123456789ab", "ast_0123456789ab", "proxy.mp4")
    assert url == "/files/usr_0123456789ab/assets/ast_0123456789ab/proxy.mp4"
    assert parse_file_url(url) == ("usr_0123456789ab", "ast_0123456789ab", "proxy.mp4", "asset")


def test_parse_file_url_rejects_bad_shapes():
    assert parse_file_url("/files/usr_0123456789ab/assets/ast_0123456789ab/../x") is None
    assert parse_file_url("/files/usr_x/assets/ast_0123456789ab/proxy.mp4") is None
    assert parse_file_url("/api/v1/me") is None
    assert parse_file_url("/files/usr_0123456789ab/assets/ast_0123456789ab/") is None
    assert parse_file_url("/files/usr_0123456789ab/assets/ast_0123456789ab/..") is None
    assert parse_file_url("/files/usr_0123456789ab/assets/ast_0123456789ab/.") is None


def test_public_files_exclude_source():
    assert "proxy.mp4" in PUBLIC_FILES and "peaks.json" in PUBLIC_FILES
    assert not any(name.startswith("source") for name in PUBLIC_FILES)


def test_parse_file_url_understands_renders():
    url = "/files/usr_0123456789ab/projects/prj_0123456789ab/renders/rnd_0123456789ab.mp4"
    assert parse_file_url(url) == ("usr_0123456789ab", "prj_0123456789ab", "rnd_0123456789ab.mp4", "render")
    # В каталоге рендеров наружу идёт только «{id}.mp4», ничего другого.
    base = "/files/usr_0123456789ab/projects/prj_0123456789ab/renders"
    assert parse_file_url(f"{base}/../x") is None
    assert parse_file_url(f"{base}/evil.exe") is None
    assert parse_file_url(f"{base}/rnd_0123456789ab.part") is None
    assert parse_file_url(f"{base}/notanid.mp4") is None
    assert parse_file_url("/files/usr_x/projects/prj_0123456789ab/renders/rnd_0123456789ab.mp4") is None
