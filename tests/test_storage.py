import pytest

from server.app.config import Settings
from server.app.storage import (
    PUBLIC_FILES,
    asset_dir,
    conversion_dir,
    conversion_url,
    file_url,
    kind_from_ext,
    parse_file_url,
    render_dir,
    render_url,
    safe_ext,
    server_space,
    subs_dir,
    transcript_path,
    tree_bytes,
    upload_path,
    user_dir,
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


def test_project_subs_live_beside_the_renders(tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path / "d")
    assert subs_dir(s, "usr_0123456789ab", "prj_0123456789ab") == (
        tmp_path / "d" / "usr_0123456789ab" / "projects" / "prj_0123456789ab" / "subs"
    )
    for bad in ("../../etc", "prj_x"):
        with pytest.raises(ValueError):
            subs_dir(s, "usr_0123456789ab", bad)
    # Каталог субтитров наружу не отдаётся: из /files/ доступны только готовые ролики.
    base = "/files/usr_0123456789ab/projects/prj_0123456789ab"
    assert parse_file_url(f"{base}/subs/3.srt") is None


def test_transcript_lives_next_to_the_source(tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path / "d")
    assert transcript_path(s, "usr_0123456789ab", "ast_0123456789ab") == (
        asset_dir(s, "usr_0123456789ab", "ast_0123456789ab") / "transcript.json"
    )
    # Наружу файлом не отдаётся: транскрипт уходит через API, а не через /files/.
    assert "transcript.json" not in PUBLIC_FILES


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


def test_conversion_paths_come_from_ids(tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path / "d")
    assert conversion_dir(s, "usr_0123456789ab", "ast_0123456789ab") == (
        tmp_path / "d" / "usr_0123456789ab" / "assets" / "ast_0123456789ab" / "conversions"
    )
    assert conversion_url("usr_0123456789ab", "ast_0123456789ab", "cnv_0123456789ab", "mp3") == (
        "/files/usr_0123456789ab/assets/ast_0123456789ab/conversions/cnv_0123456789ab.mp3"
    )


def test_parse_file_url_understands_conversions():
    url = "/files/usr_0123456789ab/assets/ast_0123456789ab/conversions/cnv_0123456789ab.mp3"
    assert parse_file_url(url) == (
        "usr_0123456789ab", "ast_0123456789ab", "cnv_0123456789ab.mp3", "conversion"
    )
    assert parse_file_url(
        "/files/usr_0123456789ab/assets/ast_0123456789ab/conversions/cnv_0123456789ab.webm"
    ) == ("usr_0123456789ab", "ast_0123456789ab", "cnv_0123456789ab.webm", "conversion")
    assert parse_file_url(
        "/files/usr_0123456789ab/assets/ast_0123456789ab/conversions/cnv_0123456789ab.aac"
    ) == ("usr_0123456789ab", "ast_0123456789ab", "cnv_0123456789ab.aac", "conversion")
    base = "/files/usr_0123456789ab/assets/ast_0123456789ab/conversions"
    assert parse_file_url(f"{base}/../source.mp4") is None
    assert parse_file_url(f"{base}/evil.exe") is None
    assert parse_file_url(f"{base}/cnv_0123456789ab.part") is None
    assert parse_file_url(f"{base}/notanid.wav") is None
    assert parse_file_url("/files/usr_x/assets/ast_0123456789ab/conversions/cnv_0123456789ab.mp3") is None


def test_tree_bytes_sums_nested_files_and_ignores_missing(tmp_path):
    root = tmp_path / "root"
    (root / "a" / "b").mkdir(parents=True)
    (root / "one.bin").write_bytes(b"x" * 10)
    (root / "a" / "two.bin").write_bytes(b"x" * 20)
    (root / "a" / "b" / "three.bin").write_bytes(b"x" * 30)
    assert tree_bytes(root) == 60
    assert tree_bytes(tmp_path / "missing") == 0


def test_user_dir_holds_assets_and_projects(tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path)
    user = "usr_0123456789ab"
    assert asset_dir(s, user, "ast_0123456789ab").parent.parent == user_dir(s, user)
    assert render_dir(s, user, "prj_0123456789ab").parent.parent.parent == user_dir(s, user)
    with pytest.raises(ValueError):
        user_dir(s, "../etc")


def test_server_space_counts_data_and_separate_tmp_once(tmp_path):
    data, tmp = tmp_path / "data", tmp_path / "tmp"
    (data / "usr_0123456789ab").mkdir(parents=True)
    (data / "usr_0123456789ab" / "f.bin").write_bytes(b"x" * 100)
    (data / "video.db").write_bytes(b"x" * 7)
    tmp.mkdir()
    (tmp / "upload.part").write_bytes(b"x" * 50)
    files, free = server_space(Settings(_env_file=None, data_dir=data, tmp_dir=tmp))
    assert files == 157 and free > 0
    # Временный каталог внутри данных уже посчитан обходом данных — второй раз его не прибавляем.
    (data / "tmp").mkdir()
    (data / "tmp" / "upload.part").write_bytes(b"x" * 40)
    files, _ = server_space(Settings(_env_file=None, data_dir=data))
    assert files == 147
