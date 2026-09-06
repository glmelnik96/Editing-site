import httpx
import pytest

from server.app.config import Settings
from server.app.transcribe.provider import ProviderError, TranscribeProvider


def make_settings(**kw) -> Settings:
    """Умолчания одним словарём: иначе вызов с тем же ключом даст «multiple values»."""
    return Settings(_env_file=None, **{"transcribe_api_key": "k", **kw})


def provider(handler, **kw) -> TranscribeProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return TranscribeProvider(make_settings(**kw), client, sleep=lambda _s: None)


def test_sends_multipart_with_language_and_verbose_json():
    """Без language verbose_json отвечает 400 — проверено живьём в родственном плагине."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        body = request.content.decode("utf-8", errors="replace")
        seen["fields"] = [name for name in ("model", "language", "response_format") if name in body]
        seen["model"] = "openai/whisper-large-v3" in body
        return httpx.Response(200, json={"segments": [{"start": 0.0, "end": 1.0, "text": "привет"}]})

    result = provider(handler).transcribe(b"\0" * 10, "chunk.mp3")
    assert seen["path"].endswith("/audio/transcriptions")
    assert seen["auth"] == "Bearer k"
    assert seen["fields"] == ["model", "language", "response_format"]
    assert seen["model"] is True
    assert result["segments"][0]["text"] == "привет"


def test_too_large_is_told_apart():
    """413 разбирается по статусу: в успешном verbose_json встречаются числа вроде 23413,
    и разбор тела ради классификации ошибки только вводит в заблуждение."""
    p = provider(lambda r: httpx.Response(413, text="too large"))
    with pytest.raises(ProviderError) as exc:
        p.transcribe(b"x", "chunk.mp3")
    assert exc.value.kind == "too_large"


@pytest.mark.parametrize("status,kind", [(401, "auth"), (403, "auth"), (400, "bad_request")])
def test_permanent_refusals_are_not_retried(status, kind):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(status, text="no")

    with pytest.raises(ProviderError) as exc:
        provider(handler).transcribe(b"x", "chunk.mp3")
    assert exc.value.kind == kind and len(calls) == 1


def test_retries_on_busy_then_succeeds():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, text="wait")
        return httpx.Response(200, json={"segments": []})

    provider(handler, transcribe_retries=3).transcribe(b"x", "chunk.mp3")
    assert len(calls) == 3


def test_gives_up_after_the_last_retry():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500, text="bad")

    with pytest.raises(ProviderError) as exc:
        provider(handler, transcribe_retries=2).transcribe(b"x", "chunk.mp3")
    assert exc.value.kind == "server" and len(calls) == 2


def test_network_failure_is_retried_then_reported():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectTimeout("не дождались", request=request)

    with pytest.raises(ProviderError) as exc:
        provider(handler, transcribe_retries=2).transcribe(b"x", "chunk.mp3")
    assert exc.value.kind == "unavailable" and len(calls) == 2


def test_empty_key_refuses_before_the_network():
    """Пустой ключ выключает транскрипцию, а не отправляет запрос без авторизации."""
    called = []
    p = provider(lambda r: called.append(1), transcribe_api_key="")
    with pytest.raises(ProviderError) as exc:
        p.transcribe(b"x", "chunk.mp3")
    assert exc.value.kind == "unconfigured" and not called


def test_non_json_success_is_an_error():
    p = provider(lambda r: httpx.Response(200, text="не json"))
    with pytest.raises(ProviderError) as exc:
        p.transcribe(b"x", "chunk.mp3")
    assert exc.value.kind == "bad_response"


def test_oversize_payload_is_refused_before_sending():
    """Чанк больше предела не имеет смысла отправлять: провайдер ответит 413, а трафик уйдёт."""
    called = []
    p = provider(lambda r: called.append(1), transcribe_max_upload_bytes=1024)
    with pytest.raises(ProviderError) as exc:
        p.transcribe(b"x" * 2048, "chunk.mp3")
    assert exc.value.kind == "too_large" and not called


def test_error_text_does_not_leak_the_key():
    """Ключ не должен попасть ни в текст ошибки, ни в журнал."""
    p = provider(lambda r: httpx.Response(500, text="boom"), transcribe_api_key="секретноезначение")
    with pytest.raises(ProviderError) as exc:
        p.transcribe(b"x", "chunk.mp3")
    assert "секретноезначение" not in str(exc.value)
