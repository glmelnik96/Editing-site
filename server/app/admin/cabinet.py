"""Кабинет: три белых списка в одной таблице и правки с результатом по каждому сервису.

Свой список правится прямым вызовом, соседские — через адаптер. Частичный успех не откатываем:
три сервиса — это три независимые операции, а не транзакция, и притворяться иначе значит врать
(спека §7).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from server.app.admin import store
from server.app.admin.services import Person, RemoteClient, ServiceError
from server.app.auth.users import normalize_email
from server.app.config import Settings
from server.app.errors import ApiError

OWN_KEY = "video"
OWN_TITLE = "Видео"


class CabinetError(Exception):
    """Отказ до того, как что-либо изменено: неизвестный сервис, чужой адрес, администратор."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class ServiceState:
    key: str
    title: str
    state: str  # ok | unconfigured | unavailable | forbidden | bad_response
    message: str = ""


@dataclass
class PersonRow:
    email: str
    admin: bool
    access: dict[str, bool | None]


@dataclass
class CabinetView:
    services: list[ServiceState]
    people: list[PersonRow] = field(default_factory=list)


@dataclass
class ChangeResult:
    service: str
    action: str  # grant | revoke
    ok: bool
    error: str | None = None


def collect(conn: sqlite3.Connection, settings: Settings, clients: list[RemoteClient]) -> CabinetView:
    """Читает три списка и сводит их в таблицу. Упавший сосед забирает с собой только свой столбец."""
    admin_email = normalize_email(settings.admin_email)
    services = [ServiceState(key=OWN_KEY, title=OWN_TITLE, state="ok")]
    lists: dict[str, list[Person] | None] = {
        OWN_KEY: [Person(email=row["email"], added_by=row["added_by"] or "") for row in store.listing(conn)]
    }
    for client in clients:
        try:
            lists[client.service.key] = client.list()
            services.append(ServiceState(key=client.service.key, title=client.service.title, state="ok"))
        except ServiceError as exc:
            lists[client.service.key] = None
            services.append(
                ServiceState(client.service.key, client.service.title, exc.kind, str(exc))
            )

    emails: set[str] = set()
    for people in lists.values():
        if people is not None:
            emails.update(normalize_email(p.email) for p in people)

    rows = []
    for email in sorted(emails):
        access: dict[str, bool | None] = {}
        for key, people in lists.items():
            # None значит «не знаем»: у недоступного соседа отсутствие адреса ничего не доказывает.
            access[key] = None if people is None else any(normalize_email(p.email) == email for p in people)
        rows.append(PersonRow(email=email, admin=bool(admin_email) and email == admin_email, access=access))
    return CabinetView(services=services, people=rows)


def apply(
    conn: sqlite3.Connection,
    settings: Settings,
    clients: list[RemoteClient],
    *,
    email: str,
    grant: list[str],
    revoke: list[str],
    added_by: str,
) -> list[ChangeResult]:
    """Применяет правки по сервисам и возвращает результат по каждому."""
    normalized = store.valid_email(email)
    known = {OWN_KEY} | {c.service.key for c in clients}
    unknown = (set(grant) | set(revoke)) - known
    if unknown:
        raise CabinetError("unknown_service", f"Неизвестный сервис: {', '.join(sorted(unknown))}")
    both = set(grant) & set(revoke)
    if both:
        raise CabinetError("contradictory_change", f"И дать, и снять сразу: {', '.join(sorted(both))}")
    if normalized == normalize_email(settings.admin_email):
        raise CabinetError("cannot_change_admin", "Доступ администратора задан конфигурацией сервисов")

    by_key = {c.service.key: c for c in clients}
    results: list[ChangeResult] = []
    for action, keys in (("grant", grant), ("revoke", revoke)):
        for key in keys:
            results.append(_one(conn, settings, by_key.get(key), key, action, normalized, added_by))
    return results


def _one(
    conn: sqlite3.Connection,
    settings: Settings,
    client: RemoteClient | None,
    key: str,
    action: str,
    email: str,
    added_by: str,
) -> ChangeResult:
    try:
        if key == OWN_KEY:
            if action == "grant":
                store.add(conn, email, added_by=added_by)
            else:
                _remove_own(conn, settings, email)
        elif client is not None:
            if action == "grant":
                client.add(email)
            else:
                client.remove(email)
    except (ServiceError, ApiError) as exc:
        message = exc.message if isinstance(exc, ApiError) else str(exc)
        return ChangeResult(service=key, action=action, ok=False, error=message)
    return ChangeResult(service=key, action=action, ok=True)


def _remove_own(conn: sqlite3.Connection, settings: Settings, email: str) -> None:
    """Отсутствие адреса — не ошибка: снятие достигло цели, как и у соседей."""
    try:
        store.remove(conn, settings, email)
    except ApiError as exc:
        if exc.code != "not_found":
            raise
