from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from curl_cffi.requests.exceptions import RequestException

import app
import provider

client = TestClient(app.app, raise_server_exceptions=False)
URL = "https://www.tiktok.com/@demo/video/1234567890123456789"
PAYLOAD = {"url": URL, "platform": "tiktok", "service": "views"}
HEADERS = {}
PRIVATE = "https://leofame.com/private?token=SECRET"


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setattr(provider.time, "sleep", lambda _: None)


@pytest.fixture
def session(monkeypatch):
    fake = MagicMock()
    fake.__enter__.return_value = fake
    fake.cookies.get.return_value = "SECRET"
    fake.get.return_value.status_code = 200
    fake.get.return_value.text = '<input value="35" name="quantity">'
    fake.post.return_value.status_code = 200
    fake.post.return_value.headers = {}
    fake.post.return_value.json.return_value = {"success": True, "message": PRIVATE, "token": "SECRET"}
    monkeypatch.setattr(provider.requests, "Session", lambda **_: fake)
    return fake


def post(payload=None, headers=None):
    response = client.post("/api/claim", json=PAYLOAD if payload is None else payload, headers=HEADERS if headers is None else headers)
    body = response.json()
    assert set(body) == {"message", "request_id", "data"}
    assert set(body["data"]) == {"url", "platform", "service"}
    assert isinstance(body["message"], str) and body["message"]
    assert body["request_id"] == response.headers["X-Request-ID"]
    assert len(body["request_id"]) == 32
    assert "leofame" not in response.text.lower()
    assert "SECRET" not in response.text
    assert "provider_response" not in response.text
    return response, body


def test_success_and_payload(session):
    response, body = post()
    assert response.status_code == 200
    assert body["message"] == app.STATES["success"][1]
    assert body["data"] == PAYLOAD
    payload = session.post.call_args.kwargs["data"]
    assert payload["free_link"] == URL
    assert payload["quantity"] == "200"
    assert payload["token"] == "SECRET"
    assert session.post.call_args.kwargs["allow_redirects"] is False


def test_likes_quantity_and_case(session):
    _, body = post({**PAYLOAD, "platform": " TikTok ", "service": " Likes "})
    assert body["data"] == {**PAYLOAD, "service": "likes"}
    assert session.post.call_args.kwargs["data"]["quantity"] == "35"


@pytest.mark.parametrize("payload", [
    {**PAYLOAD, "service": "followers"},
    {"url": "https://www.instagram.com/p/abc/", "platform": "instagram", "service": "likes"},
    {**PAYLOAD, "platform": "newplatform"},
])
def test_unsupported(payload, session):
    response, body = post(payload)
    assert response.status_code == 422
    assert body["message"] == app.STATES["unsupported"][1]
    session.get.assert_not_called()


@pytest.mark.parametrize("payload", [
    {}, {**PAYLOAD, "service": "both"}, {**PAYLOAD, "extra": PRIVATE},
    {**PAYLOAD, "url": "https://tiktok.com.evil.test/video"},
    {**PAYLOAD, "url": "http://www.tiktok.com/video"},
    {**PAYLOAD, "url": "https://user@www.tiktok.com/video"},
    {**PAYLOAD, "url": "https://www.tiktok.com:444/video"},
    {**PAYLOAD, "url": "https://www.tiktok.com:bad/video"},
    {**PAYLOAD, "url": "https://www.tiktok.com/"},
    {**PAYLOAD, "url": "https://www.tiktok.com/a\nb"},
    {**PAYLOAD, "platform": "instagram"},
    {**PAYLOAD, "url": 123},
    {**PAYLOAD, "url": "x" * 2049},
])
def test_validation(payload, session):
    response, body = post(payload)
    assert response.status_code == 422
    assert body["message"] == app.STATES["invalid_request"][1]
    session.get.assert_not_called()


@pytest.mark.parametrize("raw,status,http", [
    ({"success": True}, "success", 200),
    ({"status": "success"}, "success", 200),
    ({"success": False, "message": PRIVATE}, "failed", 422),
    ({"status": "error", "success": True}, "failed", 422),
    ({"status": "pending", "success": True}, "pending", 202),
    ({"status": "queued"}, "pending", 202),
    ({"status": "cooldown", "retry_after": 60}, "rate_limited", 429),
    ({"success": False, "message": "Rate limit reached " + PRIVATE}, "rate_limited", 429),
    ({"status": "error", "message": "Please wait 15 minutes " + PRIVATE}, "rate_limited", 429),
    ({"success": "true", "message": PRIVATE}, "unknown", 502),
    ({"status": "not_success"}, "unknown", 502),
    ({"message": PRIVATE}, "unknown", 502),
    ([], "unknown", 502),
])
def test_normalized_results(raw, status, http, session):
    session.post.return_value.json.return_value = raw
    response, body = post()
    assert response.status_code == http
    assert body["message"] == app.STATES[status][1]
    assert body["data"] == PAYLOAD


def test_limit_header(session):
    session.post.return_value.status_code = 429
    session.post.return_value.headers = {"Retry-After": "120"}
    response, body = post()
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "120"
    assert body["data"] == PAYLOAD


@pytest.mark.parametrize("retry", [PRIVATE, True, -5, 999999999999999999999, "9" * 5000])
def test_invalid_retry_not_reflected(retry, session):
    session.post.return_value.json.return_value = {"status": "cooldown", "retry_after": retry}
    response, body = post()
    assert "Retry-After" not in response.headers


def test_html_is_not_forwarded(session):
    session.post.return_value.json.side_effect = ValueError(PRIVATE)
    session.post.return_value.text = "<html>" + PRIVATE + "SECRET</html>"
    response, body = post()
    assert response.status_code == 502
    assert body["message"] == app.STATES["unknown"][1]


@pytest.mark.parametrize("phase,expected,http", [("get", "unavailable", 503), ("post", "unknown", 502)])
def test_network_failure(phase, expected, http, session):
    getattr(session, phase).side_effect = RequestException(PRIVATE)
    response, body = post()
    assert response.status_code == http
    assert body["message"] == app.STATES[expected][1]
    assert getattr(session, phase).call_count == 1
    assert not app.claim_lock.locked()


@pytest.mark.parametrize("code", [301, 403, 500])
def test_page_unavailable(code, session):
    session.get.return_value.status_code = code
    response, _ = post()
    assert response.status_code == 503
    session.post.assert_not_called()


def test_missing_token(session):
    session.cookies.get.return_value = None
    response, _ = post()
    assert response.status_code == 503
    session.post.assert_not_called()


def test_quantity_missing_fails_closed(session):
    session.get.return_value.text = "<html>changed</html>"
    response, _ = post({**PAYLOAD, "service": "likes"})
    assert response.status_code == 503
    session.post.assert_not_called()


@pytest.mark.parametrize("legacy_key", [None, "ignored-old-key"])
def test_no_api_key_required(monkeypatch, session, legacy_key):
    if legacy_key is not None:
        monkeypatch.setenv("API_KEY", legacy_key)
    response, body = post(headers={})
    assert response.status_code == 200
    assert body["message"] == app.STATES["success"][1]
    assert session.post.call_count == 1


def test_busy(session):
    app.claim_lock.acquire()
    try:
        response, body = post()
        assert response.status_code == 429
        assert body["message"] == app.STATES["busy"][1]
        session.get.assert_not_called()
    finally:
        app.claim_lock.release()


def test_unexpected_exception_does_not_leak(session, caplog):
    session.post.side_effect = RuntimeError(PRIVATE + "SECRET")
    response, body = post()
    assert response.status_code == 500
    assert body["message"] == app.STATES["error"][1]
    assert body["data"] == PAYLOAD
    assert "SECRET" not in caplog.text
    assert "leofame" not in caplog.text
    assert not app.claim_lock.locked()


def test_fresh_sessions(monkeypatch):
    factory = MagicMock()
    factory.return_value.__enter__.return_value.cookies.get.return_value = None
    monkeypatch.setattr(provider.requests, "Session", factory)
    post()
    post()
    assert factory.call_count == 2


def test_health_docs_and_routing():
    health = client.get("/health")
    assert health.status_code == 200
    assert set(health.json()) == {"message", "request_id", "data"}
    for path in ("/docs", "/openapi.json", "/private-path"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["message"] == app.STATES["not_found"][1]
    response = client.get("/api/claim")
    assert response.status_code == 405
    assert response.json()["message"] == app.STATES["method_not_allowed"][1]
    assert response.headers["Allow"] == "POST"


def test_malformed_json():
    response = client.post("/api/claim", content='{"broken":', headers={**HEADERS, "Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["message"] == app.STATES["invalid_request"][1]
    assert set(response.json()) == {"message", "request_id", "data"}


def test_public_schema_contains_no_internal_details():
    import json
    schema = json.dumps(app.app.openapi()).lower()
    assert "leofame" not in schema
    assert "provider_response" not in schema
    assert "/api/claim" in schema
    assert "x-api-key" not in schema
    assert set(app.app.openapi()["components"]["schemas"]["Envelope"]["properties"]) == {"message", "request_id", "data"}


def test_partial_invalid_input_echoes_only_allowed_fields(session):
    response, body = post({"url": URL, "service": "views", "private": PRIVATE})
    assert response.status_code == 422
    assert body["data"] == {"url": URL, "platform": None, "service": "views"}
    session.get.assert_not_called()


def test_homepage_served_with_api_form():
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert '<html lang="id">' in response.text
    assert all(f'id="{field}"' in response.text for field in ("url", "platform", "service"))
    assert "fetch('/api/claim'" in response.text
    assert "leofame" not in response.text.lower()
    assert "x-api-key" not in response.text.lower()


@pytest.mark.parametrize("raw,expected", [
    ({"status": "queued", "message": "Please wait 10 seconds"}, "pending"),
    ({"success": True, "message": "Please wait for delivery"}, "success"),
    ({"status": "error", "message": "Please wait 15 minutes"}, "rate_limited"),
])
def test_wait_message_is_not_always_a_limit(raw, expected, session):
    session.post.return_value.json.return_value = raw
    response, body = post()
    assert response.status_code == app.STATES[expected][0]
    assert body["message"] == app.STATES[expected][1]


def test_public_headers():
    response = client.get("/")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
