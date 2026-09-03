#!/usr/bin/env bash
# Деплой текущего main: sudo bash /opt/editing-site/deploy/deploy.sh
set -euo pipefail

APP_DIR=/opt/editing-site
HEALTH_URL=http://127.0.0.1:8010/healthz
cd "$APP_DIR"

run_as_video() { sudo -u video env HOME="$APP_DIR" UV_PYTHON=/usr/bin/python3 UV_PYTHON_DOWNLOADS=never "$@"; }

run_as_video git fetch origin
run_as_video git merge --ff-only origin/main
run_as_video uv sync --frozen --no-dev
(cd web && run_as_video npm ci --no-audit --no-fund && run_as_video npm run build)
run_as_video .venv/bin/python -m server.db.migrate

systemctl restart video-api
sleep 2
systemctl is-active video-api

# /healthz отвечает 200 и при status=degraded, поэтому читаем тело, а не только код.
run_as_video .venv/bin/python - "$HEALTH_URL" <<'EOF'
import json
import sys
import urllib.request

body = json.load(urllib.request.urlopen(sys.argv[1], timeout=5))
print(body)
sys.exit(0 if body.get("status") == "ok" else 1)
EOF
echo "deploy ok: $(git rev-parse --short HEAD)"
