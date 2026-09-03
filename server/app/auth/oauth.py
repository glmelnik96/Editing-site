"""Yandex OAuth: три чистые функции, httpx-клиент передаётся снаружи (в тестах подменяются целиком)."""
from __future__ import annotations

from urllib.parse import urlencode

import httpx

AUTHORIZE_URL = "https://oauth.yandex.ru/authorize"
TOKEN_URL = "https://oauth.yandex.ru/token"
USERINFO_URL = "https://login.yandex.ru/info"


def build_authorize_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            # Яндекс каждый раз показывает выбор аккаунта вместо тихого SSO.
            "force_confirm": "yes",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


async def exchange_code(
    client: httpx.AsyncClient, *, code: str, client_id: str, client_secret: str, redirect_uri: str
) -> str:
    resp = await client.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        },
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


async def fetch_userinfo(client: httpx.AsyncClient, access_token: str) -> dict:
    resp = await client.get(
        USERINFO_URL, params={"format": "json"}, headers={"Authorization": f"OAuth {access_token}"}
    )
    resp.raise_for_status()
    return resp.json()
