"""Справочный клиент загрузки: тот же протокол, что использует браузер, для агентов и смоук-тестов.

Запуск:  python tools/upload_file.py <base_url> <token> <file>
Докачка: если процесс прервали на середине, запустите заново с --upload-id из stderr прошлого
запуска — дошлются только недостающие части.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def call(
    base: str, token: str, method: str, path: str, body: bytes | None = None, ctype: str | None = None
) -> dict:
    """Один запрос к API: заголовок авторизации, JSON-тело по желанию, ошибка HTTP/сети — в SystemExit."""
    req = urllib.request.Request(base + path, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if ctype:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"{method} {path}: HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"{method} {path}: не удалось соединиться ({exc.reason})") from exc


def upload(base: str, token: str, file: Path, upload_id: str | None) -> dict:
    """Создаёт загрузку (или продолжает по upload_id), досылает недостающие части, завершает её."""
    size = file.stat().st_size
    if upload_id:
        st = call(base, token, "GET", f"/api/v1/uploads/{upload_id}")
        if st["size"] != size:
            raise SystemExit(f"размер файла ({size}) не совпадает с загрузкой {upload_id} ({st['size']})")
        chunk_size, total, received = st["chunk_size"], st["total"], set(st["received"])
    else:
        created = call(
            base, token, "POST", "/api/v1/uploads",
            json.dumps({"filename": file.name, "size": size}).encode(), "application/json",
        )
        upload_id = created["upload_id"]
        chunk_size, total, received = created["chunk_size"], created["total_chunks"], set()
    print(f"upload_id={upload_id} chunks={total} chunk_size={chunk_size}", file=sys.stderr)
    with open(file, "rb") as f:
        for idx in range(total):
            if idx in received:
                continue
            f.seek(idx * chunk_size)
            data = f.read(chunk_size)
            call(
                base, token, "PUT", f"/api/v1/uploads/{upload_id}/chunks/{idx}", data,
                "application/octet-stream",
            )
            print(f"chunk {idx + 1}/{total}", file=sys.stderr)
    return call(base, token, "POST", f"/api/v1/uploads/{upload_id}/complete")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("base_url", help="адрес сервера, например https://video.cloudrudesign.ru")
    parser.add_argument("token", help="агентский токен: Authorization: Bearer <token>")
    parser.add_argument("file", type=Path, help="путь к загружаемому файлу")
    parser.add_argument("--upload-id", help="продолжить незавершённую загрузку")
    args = parser.parse_args(argv)

    done = upload(args.base_url.rstrip("/"), args.token, args.file, args.upload_id)
    print(json.dumps(done))
    return 0


if __name__ == "__main__":
    sys.exit(main())
