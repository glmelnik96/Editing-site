"""Сквозной прогон сценария внешнего агента: от загрузки файла до скачанного ролика.

Запуск: python tools/agent_smoke.py <base_url> <token> <файл.mp4>

Проходит весь путь из раздела 5 спеки одним запуском: загрузка по частям, ожидание анализа
и прокси, чтение карты пауз, проект из двух клипов, черновой рендер, скачивание готового
файла, завершение проекта. Каждый шаг печатается со временем от старта; при отказе видно
код и тело ответа, а код возврата — ненулевой.

Это же регламентная проверка после каждой выкатки: путь агента и путь браузера — один и тот же
API, и если тут всё прошло, значит наружу сервис работает.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Скрипт запускают файлом («python tools/agent_smoke.py»), а он берёт загрузчик из соседнего
# модуля пакета: корень репозитория в sys.path сам по себе не окажется.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.upload_file import call, upload

ASSET_WAIT_SEC = 900
RENDER_WAIT_SEC = 3600
POLL_SEC = 3
CLIP_SEC = 3.0

started = time.monotonic()


def say(step: str, text: str) -> None:
    print(f"[{time.monotonic() - started:6.1f} с] {step:<4} {text}", flush=True)


def fail(text: str) -> None:
    raise SystemExit(f"[{time.monotonic() - started:6.1f} с] ОТКАЗ {text}")


def fetch_file(base: str, token: str, path: str) -> tuple[dict[str, str], bytes]:
    """Скачивание файла с авторизацией: возвращает заголовки и тело.

    Имена заголовков приводим к нижнему регистру: сервер шлёт их строчными, Caddy может иначе."""
    req = urllib.request.Request(base + path, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            return {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        fail(f"GET {path}: HTTP {exc.code} {detail}")
    except urllib.error.URLError as exc:
        fail(f"GET {path}: не удалось соединиться ({exc.reason})")
    raise AssertionError("недостижимо")  # fail() всегда бросает; здесь только ради типов


def wait_asset(base: str, token: str, asset_id: str) -> dict:
    """Ждём, пока анализ и прокси доделают файл. Статус failed — конец прогона."""
    deadline = time.monotonic() + ASSET_WAIT_SEC
    seen = ""
    while time.monotonic() < deadline:
        asset = call(base, token, "GET", f"/api/v1/assets/{asset_id}")
        if asset["status"] != seen:
            seen = asset["status"]
            say("2/7", f"статус файла: {seen}")
        if seen == "proxy_ready":
            return asset
        if seen == "failed":
            fail(f"обработка файла не удалась: {asset.get('error')}")
        time.sleep(POLL_SEC)
    fail(f"файл не дошёл до proxy_ready за {ASSET_WAIT_SEC} с (последний статус: {seen})")
    raise AssertionError("недостижимо")


def wait_job(base: str, token: str, job_id: str) -> dict:
    """Ждём задание рендера, печатая прогресс по мере роста."""
    deadline = time.monotonic() + RENDER_WAIT_SEC
    shown = -1
    while time.monotonic() < deadline:
        job = call(base, token, "GET", f"/api/v1/jobs/{job_id}")
        percent = int(job["progress"] * 100)
        if job["status"] == "running" and percent >= shown + 10:
            shown = percent
            say("5/7", f"собираю: {percent} %")
        if job["status"] == "done":
            return job
        if job["status"] in ("failed", "canceled"):
            fail(f"сборка завершилась статусом {job['status']}: {job.get('error')}")
        time.sleep(POLL_SEC)
    fail(f"сборка не закончилась за {RENDER_WAIT_SEC} с")
    raise AssertionError("недостижимо")


def build_clips(duration: float, silences: list[dict]) -> list[dict]:
    """Два куска из разных мест файла. Границы отдаём с snap_to_pauses: сервер подтянет их
    к ближайшей паузе, и в ответе будет видно, какие резы он подтвердил."""
    first_out = min(CLIP_SEC, duration)
    clips = [{"asset_id": "", "in": 0.0, "out": round(first_out, 3), "snap_to_pauses": True}]
    if duration >= 2 * CLIP_SEC:
        # Со второго куска начинаем от паузы в середине файла, если она там есть.
        middle = duration / 2
        pause = next((s for s in silences if s["start"] >= middle), None)
        start = pause["start"] if pause and pause["start"] + CLIP_SEC <= duration else middle
        clips.append(
            {
                "asset_id": "",
                "in": round(start, 3),
                "out": round(min(start + CLIP_SEC, duration), 3),
                "snap_to_pauses": True,
            }
        )
    return clips


def verified(clips: list[dict]) -> str:
    ok = sum(int(bool(c.get("in_verified"))) + int(bool(c.get("out_verified"))) for c in clips)
    return f"{ok} из {2 * len(clips)} границ подтверждены паузами"


def smoke(base: str, token: str, file: Path) -> int:
    me = call(base, token, "GET", "/api/v1/me")
    say("0/7", f"вход выполнен: {me['email']}, занято {me['quota']['used_bytes'] / 2**30:.2f} ГБ")

    done = upload(base, token, file, None)
    asset_id = done["asset_id"]
    say("1/7", f"файл загружен: asset_id={asset_id}")

    asset = wait_asset(base, token, asset_id)
    duration = float(asset["duration"])
    say("2/7", f"файл готов: {duration:.1f} с, прокси {asset['files']['proxy']}")

    _, raw = fetch_file(base, token, asset["files"]["analysis"])
    analysis = json.loads(raw)
    silences = analysis["silences"]
    say("3/7", f"пауз найдено: {len(silences)}, порог {analysis['threshold_db']} дБ")

    project = call(
        base, token, "POST", "/api/v1/projects",
        json.dumps({"name": f"Смоук {file.stem}"}).encode(), "application/json",
    )
    clips = build_clips(duration, silences)
    for clip in clips:
        clip["asset_id"] = asset_id
    doc = {"output": {"aspect": "16:9", "fit": "pad", "fps": 30}, "clips": clips}
    saved = call(
        base, token, "PUT", f"/api/v1/projects/{project['id']}",
        json.dumps({"name": project["name"], "version": project["version"], "doc": doc}).encode(),
        "application/json",
    )
    say("4/7", f"проект {saved['id']}: клипов {len(saved['doc']['clips'])}, "
               f"версия {saved['version']}, {verified(saved['doc']['clips'])}")

    queued = call(
        base, token, "POST", f"/api/v1/projects/{saved['id']}/render",
        json.dumps({"quality": "draft"}).encode(), "application/json",
    )
    wait_job(base, token, queued["job_id"])
    renders = call(base, token, "GET", f"/api/v1/projects/{saved['id']}/renders")["renders"]
    render = renders[0] if renders else None  # список идёт от свежих: наш ролик первый
    if render is None:
        fail("задание завершилось, а готового ролика в списке нет")
    say("5/7", f"ролик собран: {render['id']}, {render['duration']:.1f} с, "
               f"{render['size'] / 2**20:.1f} МБ")

    headers, body = fetch_file(base, token, render["download"])
    if len(body) != render["size"]:
        fail(f"скачано {len(body)} байт, а в карточке {render['size']}")
    if "attachment" not in headers.get("content-disposition", ""):
        fail(f"файл отдан без вложения: Content-Disposition={headers.get('content-disposition')!r}")
    say("6/7", f"файл скачан: {len(body)} байт, {headers.get('content-disposition')}")

    finished = call(base, token, "POST", f"/api/v1/projects/{saved['id']}/finish")
    if finished["status"] != "finished":
        fail(f"проект остался в статусе {finished['status']}")
    left = call(base, token, "GET", f"/api/v1/projects/{saved['id']}/renders")["renders"]
    if left:
        fail(f"после завершения проекта осталось роликов: {len(left)}")
    say("7/7", "проект завершён, ролики убраны")
    say("ИТОГ", "весь путь агента пройден")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("base_url", help="адрес сервера, например https://video.cloudrudesign.ru")
    parser.add_argument("token", help="агентский токен: Authorization: Bearer <token>")
    parser.add_argument("file", type=Path, help="видеофайл для прогона")
    args = parser.parse_args(argv)
    if not args.file.is_file():
        raise SystemExit(f"файла нет: {args.file}")
    return smoke(args.base_url.rstrip("/"), args.token, args.file)


if __name__ == "__main__":
    sys.exit(main())
