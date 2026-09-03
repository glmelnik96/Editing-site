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
