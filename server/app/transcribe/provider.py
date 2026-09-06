"""Адаптер провайдера транскрипции: OpenAI-совместимый маршрут /audio/transcriptions.

Всё знание о повадках маршрута собрано здесь, чтобы обработчик воркера видел только виды отказов
и не решал заново, что повторять, а что нет. Повадки проверены живьём в родственном плагине:
language обязателен (без него ответ 400), пословных таймкодов провайдер не отдаёт (слова
размечает другой модуль), на кусок больше ~20 МБ отвечает 413, а под нагрузкой даёт 429 и 5xx.
"""
from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from server.app.config import Settings

# Отказы, после которых повтор имеет смысл: провайдер занят, споткнулся или не ответил.
_RETRIABLE = frozenset({"busy", "server", "unavailable"})

# Шаг паузы между попытками: множится на номер попытки, чтобы занятому провайдеру дать передышку.
_PAUSE_STEP_SEC = 2.0

# Кусок ответа в тексте ошибки: verbose_json целиком в журнале не нужен, а причина обычно в начале.
_SNIPPET_CHARS = 300


class ProviderError(Exception):
    """Отказ провайдера. kind различает случаи, которые воркер обрабатывает по-разному:
    unconfigured — ключ не задан, too_large — кусок не влезает в запрос, auth — ключ не принят,
    bad_request — запрос не приняли, busy — 429, server — 5xx, unavailable — не ответил,
    bad_response — ответил не тем."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def build_client(settings: Settings) -> httpx.Client:
    """Отдельной функцией, чтобы обработчик воркера и тесты подменяли транспорт."""
    return httpx.Client(timeout=settings.transcribe_timeout_sec)


class TranscribeProvider:
    """Один кусок звука — один запрос. Клиент передаётся снаружи: в тестах это httpx.MockTransport,
    в воркере — общий клиент, чтобы соединение переиспользовалось между кусками."""

    def __init__(
        self,
        settings: Settings,
        client: httpx.Client,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._client = client
        # Паузу между попытками отдаём наружу: иначе тест ретраев ждал бы эти секунды по-настоящему.
        self._sleep = sleep

    def transcribe(self, data: bytes, filename: str, *, language: str | None = None) -> dict:
        """Разбор куска звука. Отдаёт тело verbose_json как есть — segments разбирает вызывающий."""
        s = self._settings
        if not s.transcribe_api_key:
            raise ProviderError("unconfigured", "ключ провайдера транскрипции не задан")
        if len(data) > s.transcribe_max_upload_bytes:
            # Провайдер на такой кусок ответит 413, так что смысла тратить на него сеть нет.
            raise ProviderError(
                "too_large",
                f"кусок {len(data)} Б больше предела {s.transcribe_max_upload_bytes} Б",
            )
        url = s.transcribe_base_url.rstrip("/") + "/audio/transcriptions"
        fields = {
            "model": s.transcribe_model,
            # language шлём всегда: без него маршрут отвечает 400, даже с verbose_json.
            "language": language or s.transcribe_language,
            "response_format": "verbose_json",
            "temperature": "0",
        }
        for attempt in range(1, s.transcribe_retries + 1):
            try:
                return self._attempt(url, data, filename, fields)
            except ProviderError as exc:
                # Постоянный отказ повторять бессмысленно, а на последней попытке повторять нечем:
                # в обоих случаях наружу уходит та же ошибка, что и была.
                if exc.kind not in _RETRIABLE or attempt == s.transcribe_retries:
                    raise
            self._sleep(_PAUSE_STEP_SEC * attempt)
        raise ProviderError("server", "попытки кончились")  # недостижимо: transcribe_retries >= 1

    def _attempt(self, url: str, data: bytes, filename: str, fields: dict[str, str]) -> dict:
        try:
            resp = self._client.post(
                url,
                headers={"Authorization": f"Bearer {self._settings.transcribe_api_key}"},
                files={"file": (filename, data)},
                data=fields,
            )
        except httpx.HTTPError as exc:
            raise ProviderError("unavailable", "провайдер транскрипции не ответил") from exc
        except Exception as exc:
            # Кривой адрес в настройках даёт ошибку мимо httpx.HTTPError, и без этой ветки опечатка
            # в конфиге роняла бы полосу воркера вместо того, чтобы пометить задачу отказом.
            raise ProviderError("unavailable", "запрос к провайдеру транскрипции не удался") from exc
        if resp.status_code >= 400:
            raise self._refusal(resp)
        # Тело разбираем только у успешного ответа: у отказа вид определяет статус, и лезть в текст
        # незачем — в успешном verbose_json встречаются числа вроде 23413, и такой разбор путает.
        try:
            body = resp.json()
        except ValueError as exc:
            raise ProviderError("bad_response", f"ответ не разобрать: {self._snippet(resp)}") from exc
        if not isinstance(body, dict):
            raise ProviderError("bad_response", f"ответ пришёл не объектом: {self._snippet(resp)}")
        return body

    def _refusal(self, resp: httpx.Response) -> ProviderError:
        """Вид отказа — по статусу. Список проверен живьём, гадать по телу не нужно."""
        code = resp.status_code
        tail = self._snippet(resp)
        if code == 413:
            return ProviderError("too_large", f"кусок не приняли по размеру ({code}): {tail}")
        if code in (401, 403):
            return ProviderError("auth", f"ключ не приняли ({code}): {tail}")
        if code == 429:
            return ProviderError("busy", f"провайдер занят ({code}): {tail}")
        if code >= 500:
            return ProviderError("server", f"провайдер ответил ошибкой ({code}): {tail}")
        return ProviderError("bad_request", f"запрос не приняли ({code}): {tail}")

    def _snippet(self, resp: httpx.Response) -> str:
        """Ключ вырезаем даже из чужого текста: шлюзы иногда возвращают запрос вместе с заголовками,
        и тогда секрет уехал бы в журнал через сообщение об ошибке."""
        key = self._settings.transcribe_api_key
        text = resp.text[:_SNIPPET_CHARS]
        return text.replace(key, "***") if key else text
