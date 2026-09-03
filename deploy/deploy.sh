#!/usr/bin/env bash
# Деплой текущего main: sudo bash /opt/editing-site/deploy/deploy.sh
# Доступ к репозиторию для пользователя video: публичный HTTPS-URL либо deploy key в /etc/editing-site/deploy_key
# (см. bootstrap.sh). Все git-команды выполняются от video: каталог принадлежит ему, root получил бы
# «dubious ownership».
set -euo pipefail

APP_DIR=/opt/editing-site
HEALTH_URL=http://127.0.0.1:8010/healthz
DEPLOY_KEY=/etc/editing-site/deploy_key
KNOWN_HOSTS=/etc/editing-site/known_hosts
DOMAIN_FILE=/etc/editing-site/domain
cd "$APP_DIR"

run_as_video() {
  local -a envs=(HOME="$APP_DIR" UV_PYTHON=/usr/bin/python3 UV_PYTHON_DOWNLOADS=never)
  if [ -f "$DEPLOY_KEY" ]; then
    envs+=(GIT_SSH_COMMAND="ssh -i $DEPLOY_KEY -o IdentitiesOnly=yes -o UserKnownHostsFile=$KNOWN_HOSTS -o StrictHostKeyChecking=accept-new")
  fi
  sudo -u video env "${envs[@]}" "$@"
}

health_ok() {
  run_as_video .venv/bin/python - "$HEALTH_URL" <<'EOF'
import json
import sys
import urllib.request

try:
    body = json.load(urllib.request.urlopen(sys.argv[1], timeout=2))
except Exception as exc:  # noqa: BLE001
    print("healthz:", exc)
    sys.exit(1)
print(json.dumps(body))
sys.exit(0 if body.get("status") == "ok" else 1)
EOF
}

run_as_video git fetch origin
run_as_video git merge --ff-only origin/main
run_as_video uv sync --frozen --no-dev
(cd web && run_as_video npm ci --no-audit --no-fund && run_as_video npm run build)
run_as_video .venv/bin/python -m server.db.migrate

# Конфиги Caddy и systemd живут в репозитории: переустанавливаем их при каждом деплое.
if [ -f "$DOMAIN_FILE" ]; then
  sed "s/VIDEO_DOMAIN_PLACEHOLDER/$(cat "$DOMAIN_FILE")/" "$APP_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile.new
  caddy validate --config /etc/caddy/Caddyfile.new --adapter caddyfile
  mv /etc/caddy/Caddyfile.new /etc/caddy/Caddyfile
  systemctl reload caddy
fi
install -m 644 "$APP_DIR/deploy/video-api.service" /etc/systemd/system/video-api.service
systemctl daemon-reload
systemctl restart video-api

# /healthz отвечает 503 при status=degraded, а тело печатаем для журнала; ждём готовности до 20 попыток.
for _ in $(seq 1 20); do
  if systemctl is-active --quiet video-api && health_ok; then
    echo "deploy ok: $(run_as_video git rev-parse --short HEAD)"
    exit 0
  fi
  sleep 1
done
echo "deploy FAILED: сервис не ответил status=ok за 20 с" >&2
journalctl -u video-api -n 30 --no-pager >&2
exit 1
