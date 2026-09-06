"""Соседние сервисы на этой же ВМ: чтение и правка их белых списков.

Кабинет ходит к ним по loopback со служебным токеном в заголовке X-Service-Token (спека §4).
Токен не даёт ни сессии, ни роли — это право выполнить операцию со списком, и больше ничего.

Формы ответов у соседей разные, поэтому у каждого своё чтение списка; наружу адаптер отдаёт
одинаковые записи, и кабинет про различия не знает.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from server.app.config import Settings


@dataclass(frozen=True)
class Person:
    email: str
    note: str = ""
    added_by: str = ""


class ServiceError(Exception):
    """Отказ соседа. kind различает случаи, которые кабинет показывает по-разному (спека §7):
    unconfigured — секрет не задан, unavailable — не ответил, forbidden — не принял токен,
    bad_response — ответил не тем."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class RemoteService:
    key: str
    title: str
    base_url: str
    list_path: str
    item_path: str
    token: str
    read_list: Callable[[object], list[Person]]


def _read_board(body: object) -> list[Person]:
    """У VideoBoard список лежит в поле emails, заметок у него нет."""
    rows = body.get("emails", []) if isinstance(body, dict) else []
    return [
        Person(email=r["email"], added_by=r.get("added_by") or "")
        for r in rows
        if isinstance(r, dict) and r.get("email")
    ]


def _read_stream(body: object) -> list[Person]:
    """У Presentation Remote список приходит голым массивом и с заметкой."""
    rows = body if isinstance(body, list) else []
    return [
        Person(email=r["email"], note=r.get("note") or "", added_by=r.get("added_by") or "")
        for r in rows
        if isinstance(r, dict) and r.get("email")
    ]


def remote_services(settings: Settings) -> list[RemoteService]:
    """Порядок здесь задаёт порядок столбцов в кабинете.

    Слэш в конце адреса срезаем: иначе пути склеятся в «//api/...» и сосед ответит 404,
    а в кабинете это выглядело бы как невнятная поломка соседа."""
    return [
        RemoteService(
            key="board",
            title="Доска",
            base_url=settings.board_base_url.rstrip("/"),
            list_path="/api/v1/admin/whitelist",
            item_path="/api/v1/admin/whitelist/{email}",
            token=settings.board_service_token,
            read_list=_read_board,
        ),
        RemoteService(
            key="stream",
            title="Трансляции",
            base_url=settings.stream_base_url.rstrip("/"),
            list_path="/api/admin/allowed",
            item_path="/api/admin/allowed/{email}",
            token=settings.stream_service_token,
            read_list=_read_stream,
        ),
    ]


def build_client(settings: Settings) -> httpx.Client:
    """Отдельной функцией, чтобы тесты подменяли её транспортом-заглушкой."""
    return httpx.Client(timeout=settings.service_timeout_sec)


class RemoteClient:
    """Один сосед. Клиент передаётся снаружи: в тестах это httpx.MockTransport."""

    def __init__(self, service: RemoteService, client: httpx.Client) -> None:
        self.service = service
        self._client = client

    def _request(
        self, method: str, path: str, json: dict | None = None, missing_ok: bool = False
    ) -> httpx.Response:
        if not self.service.token:
            raise ServiceError("unconfigured", f"{self.service.title}: служебный токен не задан")
        try:
            resp = self._client.request(
                method,
                self.service.base_url + path,
                headers={"X-Service-Token": self.service.token},
                json=json,
            )
        except httpx.HTTPError as exc:
            raise ServiceError("unavailable", f"{self.service.title}: не отвечает") from exc
        # 401 и 403 у соседей значат одно и то же — «токен не принят»; различие в кодах у них
        # историческое, и тащить его в интерфейс незачем.
        if resp.status_code in (401, 403):
            raise ServiceError("forbidden", f"{self.service.title}: служебный токен не принят")
        if resp.status_code == 404 and missing_ok:
            return resp
        if resp.status_code >= 400:
            raise ServiceError("bad_response", f"{self.service.title}: ответил кодом {resp.status_code}")
        return resp

    def list(self) -> list[Person]:
        resp = self._request("GET", self.service.list_path)
        try:
            body = resp.json()
        except ValueError as exc:
            raise ServiceError("bad_response", f"{self.service.title}: ответ не разобрать") from exc
        return self.service.read_list(body)

    def add(self, email: str) -> None:
        self._request("POST", self.service.list_path, json={"email": email})

    def remove(self, email: str) -> None:
        path = self.service.item_path.format(email=quote(email, safe=""))
        # missing_ok: человека и так нет в списке — снятие достигло цели, 404 тут не отказ.
        self._request("DELETE", path, missing_ok=True)
