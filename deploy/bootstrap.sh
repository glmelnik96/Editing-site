#!/usr/bin/env bash
# Первичная настройка чистой Ubuntu 24.04 под Editing site.
# Запуск от root: sudo bash bootstrap.sh <domain> <git-url>
# До запуска: A-запись домена указывает на эту VM (Caddy получит сертификат).
# Приватный репозиторий: заранее положить deploy key в /etc/editing-site/deploy_key (или использовать публичный HTTPS-URL).
set -euo pipefail

DOMAIN="${1:?usage: bootstrap.sh <domain> <git-url>}"
REPO="${2:?usage: bootstrap.sh <domain> <git-url>}"
APP_DIR=/opt/editing-site
DATA_DIR=/srv/video

export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=600 update
apt-get -o DPkg::Lock::Timeout=600 install -y --no-install-recommends \
  git ffmpeg sqlite3 ufw pipx nodejs npm fonts-dejavu-core fonts-noto-core \
  debian-keyring debian-archive-keyring apt-transport-https curl gnupg ca-certificates

# Caddy из официального репозитория (инструкция caddyserver.com/docs/install)
if ! command -v caddy >/dev/null 2>&1; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get -o DPkg::Lock::Timeout=600 update
  apt-get -o DPkg::Lock::Timeout=600 install -y caddy
fi

# uv через pipx в /usr/local/bin, без curl | sh
if ! command -v uv >/dev/null 2>&1; then
  PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install uv
fi

# Сервисный пользователь без домашних файлов: git clone требует пустой каталог
# В Ubuntu группа video уже существует (доступ к GPU): используем её, иначе создаём свою.
getent group video >/dev/null || groupadd --system video
if ! id -u video >/dev/null 2>&1; then
  useradd --system --gid video --home-dir "$APP_DIR" --shell /usr/sbin/nologin video
fi
mkdir -p "$APP_DIR" "$DATA_DIR/data" "$DATA_DIR/tmp/uploads"
chown video:video "$APP_DIR"
chown -R video:video "$DATA_DIR"
chmod 750 "$DATA_DIR" "$DATA_DIR/data" "$DATA_DIR/tmp" "$DATA_DIR/tmp/uploads"

# Caddy отдаёт файлы ассетов с диска сам (file_server после forward_auth):
# читает /srv/video/data через группу video.
usermod -a -G video caddy

# Ключ для приватного репозитория (необязательно): положить в /etc/editing-site/deploy_key до запуска,
# известные хосты пишутся рядом, чтобы не засорять каталог приложения.
mkdir -p /etc/editing-site
chown video:video /etc/editing-site
chmod 750 /etc/editing-site
if [ -f /etc/editing-site/deploy_key ]; then
  chown video:video /etc/editing-site/deploy_key
  chmod 600 /etc/editing-site/deploy_key
  export GIT_SSH_COMMAND="ssh -i /etc/editing-site/deploy_key -o IdentitiesOnly=yes -o UserKnownHostsFile=/etc/editing-site/known_hosts -o StrictHostKeyChecking=accept-new"
fi

if [ ! -d "$APP_DIR/.git" ]; then
  if [ -n "${GIT_SSH_COMMAND:-}" ]; then
    sudo -u video env GIT_SSH_COMMAND="$GIT_SSH_COMMAND" git clone "$REPO" "$APP_DIR"
  else
    sudo -u video git clone "$REPO" "$APP_DIR"
  fi
fi

if [ ! -f "$APP_DIR/.env" ]; then
  sudo -u video cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  cat <<EOF
!!! Заполни $APP_DIR/.env:
    VIDEO_DATA_DIR=$DATA_DIR/data
    VIDEO_PUBLIC_BASE_URL=https://$DOMAIN
    VIDEO_COOKIE_SECURE=true
    VIDEO_YANDEX_CLIENT_ID / VIDEO_YANDEX_CLIENT_SECRET / VIDEO_ADMIN_EMAIL
EOF
fi

sed "s/VIDEO_DOMAIN_PLACEHOLDER/$DOMAIN/" "$APP_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile
echo "$DOMAIN" > /etc/editing-site/domain
caddy validate --config /etc/caddy/Caddyfile
install -m 644 "$APP_DIR/deploy/video-api.service" /etc/systemd/system/video-api.service
install -m 644 "$APP_DIR/deploy/video-worker.service" /etc/systemd/system/video-worker.service
install -m 644 "$APP_DIR/deploy/video-janitor.service" /etc/systemd/system/video-janitor.service
install -m 644 "$APP_DIR/deploy/video-janitor.timer" /etc/systemd/system/video-janitor.timer
systemctl daemon-reload
systemctl enable caddy video-api video-worker video-janitor.timer
systemctl restart caddy

ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "bootstrap done. Дальше: заполнить $APP_DIR/.env и выполнить: sudo bash $APP_DIR/deploy/deploy.sh"
