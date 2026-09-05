from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"


def test_unit_passes_proxy_headers_and_runs_as_video():
    unit = (DEPLOY / "video-api.service").read_text(encoding="utf-8")
    assert "--proxy-headers --forwarded-allow-ips=127.0.0.1" in unit
    assert "--host 127.0.0.1 --port 8010" in unit
    assert "User=video" in unit
    assert "EnvironmentFile=/opt/editing-site/.env" in unit
    assert "Restart=always" in unit


def test_caddyfile_has_security_headers_and_small_default_body():
    caddy = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    for line in (
        "X-Content-Type-Options nosniff",
        "X-Frame-Options DENY",
        "Referrer-Policy strict-origin-when-cross-origin",
    ):
        assert line in caddy
    assert "max_size 1MB" in caddy
    assert "reverse_proxy 127.0.0.1:8010" in caddy
    assert "VIDEO_DOMAIN_PLACEHOLDER" in caddy
    assert "Strict-Transport-Security" in caddy
    assert "Cache-Control no-cache" in caddy


def test_scripts_have_bash_shebang_and_strict_mode_and_no_trust_proxy():
    for name in ("bootstrap.sh", "deploy.sh"):
        text = (DEPLOY / name).read_text(encoding="utf-8")
        assert text.startswith("#!/usr/bin/env bash\n")
        assert "set -euo pipefail" in text
        assert "\r" not in text
        assert "TRUST_PROXY" not in text


def test_deploy_polls_healthz_status_and_runs_git_as_video():
    text = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    assert "/healthz" in text
    assert 'body.get("status") == "ok"' in text
    assert "for _ in $(seq 1 20)" in text
    assert "run_as_video git rev-parse --short HEAD" in text
    assert "GIT_SSH_COMMAND" in text
    assert "caddy validate" in text
    assert "systemctl daemon-reload" in text


def test_unit_is_hardened():
    unit = (DEPLOY / "video-api.service").read_text(encoding="utf-8")
    assert "ProtectSystem=strict" in unit
    assert "ReadWritePaths=/srv/video" in unit


def test_caddyfile_imports_neighbour_site_blocks():
    caddy = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    body = caddy.split("VIDEO_DOMAIN_PLACEHOLDER {")[0]
    assert "import /etc/caddy/conf.d/*.caddy" in body


def test_caddyfile_serves_files_after_forward_auth_with_body_limits():
    caddy = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    assert "handle /internal/*" in caddy and "respond 404" in caddy
    assert "forward_auth 127.0.0.1:8010" in caddy and "uri /internal/authz" in caddy
    assert caddy.index("forward_auth") < caddy.index("uri strip_prefix /files") < caddy.index("file_server")
    assert "root * /srv/video/data" in caddy
    assert "handle /api/v1/uploads/*/chunks/*" in caddy and "max_size 34MB" in caddy
    assert "handle /api/v1/assets/upload" in caddy and "max_size 68MB" in caddy


def test_caddy_user_joins_video_group():
    for name in ("bootstrap.sh", "deploy.sh"):
        assert "usermod -a -G video caddy" in (DEPLOY / name).read_text(encoding="utf-8"), name


def test_janitor_units_and_install():
    unit = (DEPLOY / "video-janitor.service").read_text(encoding="utf-8")
    assert "Type=oneshot" in unit and "python -m server.janitor" in unit and "User=video" in unit
    assert "ProtectSystem=strict" in unit and "ReadWritePaths=/srv/video" in unit
    timer = (DEPLOY / "video-janitor.timer").read_text(encoding="utf-8")
    assert "OnCalendar=hourly" in timer and "Persistent=true" in timer
    for name in ("bootstrap.sh", "deploy.sh"):
        text = (DEPLOY / name).read_text(encoding="utf-8")
        assert "video-janitor.timer" in text and "video-janitor.service" in text, name


def test_deploy_reexecs_itself_after_updating():
    text = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    assert "self_before=$(sha256sum" in text
    assert "DEPLOY_REEXEC=1 exec bash" in text


def test_worker_unit_is_limited_and_installed():
    unit = (DEPLOY / "video-worker.service").read_text(encoding="utf-8")
    assert "ExecStart=/opt/editing-site/.venv/bin/python -m server.worker" in unit
    assert "User=video" in unit and "Nice=10" in unit
    assert "CPUQuota=" in unit and "MemoryMax=" in unit
    assert "ProtectSystem=strict" in unit and "ReadWritePaths=/srv/video" in unit
    assert "Restart=always" in unit and "TimeoutStopSec=" in unit
    # KillMode=mixed: SIGTERM должен дойти и до дочернего ffmpeg, а не только до python
    assert "KillMode=mixed" in unit
    for name in ("bootstrap.sh", "deploy.sh"):
        text = (DEPLOY / name).read_text(encoding="utf-8")
        assert "video-worker.service" in text, name
    assert "systemctl restart video-api video-worker" in (DEPLOY / "deploy.sh").read_text(encoding="utf-8")


def test_caddy_serves_renders_as_attachment():
    caddy = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    assert "handle /files/*/projects/*/renders/*" in caddy
    assert "header Content-Disposition attachment" in caddy
    # Частный маршрут обязан стоять раньше общего /files/*, иначе Caddy отдаст ролик без заголовка.
    assert caddy.index("handle /files/*/projects/*/renders/*") < caddy.index("handle /files/* {")
