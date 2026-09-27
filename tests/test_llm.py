import asyncio
import json

import httpx
import pytest

from clipper.config import Settings
from clipper.llm import ask


def settings(**kwargs):
    kwargs.setdefault("llm_fallback_enabled", False)
    return Settings(_env_file=None, openrouter_api_key="test-secret", **kwargs)


def test_default_and_paid_model_guard():
    assert settings().llm_model == "openrouter/free"
    assert settings(llm_model="vendor/model:free").llm_model.endswith(":free")
    assert settings(llm_provider="openai").llm_model == "gpt-4o-mini"
    for model in ("openrouter/auto", "openai/gpt-4o-mini"):
        with pytest.raises(ValueError, match="primary must be free"):
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
        asyncio.run(ask(Settings(_env_file=None, llm_fallback_enabled=False), "Test", {}))


@pytest.mark.parametrize("winner,expected", [
    ("openrouter/free", ["openrouter/free"]),
    ("gemini", ["openrouter/free"] * 4 + ["gemini"]),
    ("openai/gpt-4o-mini", ["openrouter/free"] * 4 + ["gemini"] * 4 + ["openai/gpt-4o-mini"]),
])
def test_fallback_order_and_stop_on_success(monkeypatch, winner, expected):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        model = body.get("model", "gemini")
        calls.append(model)
        if model == "gemini":
            assert request.headers["x-goog-api-key"] == "gemini-secret"
            assert "gemini-2.5-flash:generateContent" in str(request.url)
            assert "Authorization" not in request.headers
        else:
            assert request.headers["Authorization"] == "Bearer test-secret"
        if model != winner:
            return httpx.Response(429)
        if model == "gemini":
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})

    mock_client(monkeypatch, handler)
    cfg = settings(llm_fallback_enabled=True, gemini_api_key="gemini-secret")
    assert asyncio.run(ask(cfg, "Test", {})) == {"ok": True}
    assert calls == expected


def test_missing_gemini_key_skips_to_paid_and_all_failures_are_bounded(monkeypatch):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["model"])
        return httpx.Response(503)

    mock_client(monkeypatch, handler)
    with pytest.raises(RuntimeError, match="All LLM stages failed"):
        asyncio.run(ask(settings(llm_fallback_enabled=True), "Test", {}))
    assert calls == ["openrouter/free"] * 4 + ["openai/gpt-4o-mini"] * 4


def test_auth_failure_advances_without_retrying_same_stage(monkeypatch):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content).get("model", "gemini"))
        return httpx.Response(401)

    mock_client(monkeypatch, handler)
    with pytest.raises(RuntimeError):
        asyncio.run(ask(settings(llm_fallback_enabled=True, gemini_api_key="gemini-secret"), "Test", {}))
    assert calls == ["openrouter/free", "gemini", "openai/gpt-4o-mini"]
