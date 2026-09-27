import asyncio
import json

import httpx
import pytest

from clipper.config import Settings
from clipper.llm import ask


def settings(**kwargs):
    return Settings(_env_file=None, openrouter_api_key="test-secret", **kwargs)


def test_default_and_paid_model_guard():
    assert settings().llm_model == "openrouter/free"
    assert settings(llm_model="vendor/model:free").llm_model.endswith(":free")
    assert settings(llm_provider="openai").llm_model == "gpt-4o-mini"
    for model in ("openrouter/auto", "openai/gpt-4o-mini"):
        with pytest.raises(ValueError, match="paid routing is disabled"):
            settings(llm_model=model)


def mock_client(monkeypatch, handler):
    client_type = httpx.AsyncClient
    monkeypatch.setattr("clipper.llm.httpx.AsyncClient", lambda **kwargs: client_type(
        transport=httpx.MockTransport(handler), **kwargs))

    async def no_sleep(_):
        pass

    monkeypatch.setattr("clipper.llm.asyncio.sleep", no_sleep)


def test_router_request_and_json(monkeypatch):
    def handler(request):
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-secret"
        body = json.loads(request.content)
        assert body["model"] == "openrouter/free"
        assert body["response_format"] == {"type": "json_object"}
        assert "models" not in body
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"moments": []}'}}]})

    mock_client(monkeypatch, handler)
    assert asyncio.run(ask(settings(), "Select moments", [])) == {"moments": []}


@pytest.mark.parametrize("failure", [429, 503, "invalid_json", "invalid_shape"])
def test_retries_remain_on_free_router(monkeypatch, failure):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["model"])
        if len(calls) < 4:
            if isinstance(failure, int):
                return httpx.Response(failure)
            raw = "not JSON" if failure == "invalid_json" else "[]"
            return httpx.Response(200, json={"choices": [{"message": {"content": raw}}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})

    mock_client(monkeypatch, handler)
    assert asyncio.run(ask(settings(), "Test", {})) == {"ok": True}
    assert calls == ["openrouter/free"] * 4


def test_exhausted_quota_never_uses_paid_fallback(monkeypatch):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["model"])
        return httpx.Response(429)

    mock_client(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(ask(settings(), "Test", {}))
    assert calls == ["openrouter/free"] * 4


def test_missing_key_fails_before_network(monkeypatch):
    def handler(request):
        pytest.fail("No request should be sent without an API key")

    mock_client(monkeypatch, handler)
    with pytest.raises(ValueError, match="Missing openrouter API key"):
        asyncio.run(ask(Settings(_env_file=None), "Test", {}))
