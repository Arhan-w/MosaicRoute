from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

TEST_HOME = Path(tempfile.mkdtemp(prefix="mosaicroute-test-"))
os.environ["MOSAIC_DATA_DIR"] = str(TEST_HOME / "data")
os.environ["MOSAIC_ENV_FILE"] = str(TEST_HOME / ".env")
os.environ["MOSAIC_ADMIN_PASSWORD"] = "test-admin-password-never-use-in-production"
os.environ["MOSAIC_LOCAL_API_KEY"] = "mr-local-test-key-never-use-in-production-123456"
os.environ["MOSAIC_PORT"] = "8000"
os.environ["MOSAIC_DISCOVERY_INTERVAL_SECONDS"] = "0"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as mosaic  # noqa: E402


class MockProvider(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    last_headers = {}
    last_path = ""

    def log_message(self, *_args):
        pass

    def _write(self, status: int, payload: dict, content_type: str = "application/json"):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        type(self).last_headers = {k.lower(): v for k, v in self.headers.items()}
        type(self).last_path = self.path
        if self.path.startswith("/v1/paged/models"):
            query = parse_qs(urlsplit(self.path).query)
            if query.get("page") == ["2"]:
                self._write(200, {"data": [{"id": "page-model-two"}]})
            else:
                self._write(200, {"data": [{"id": "page-model-one"}], "next": "?page=2"})
        elif self.path.endswith("/external-next/models"):
            self._write(200, {"data": [{"id": "should-not-be-committed"}], "next": "https://example.invalid/steal-credentials"})
        elif self.path.endswith("/empty/models"):
            self._write(200, {"error": "not a model catalog"})
        elif self.path.endswith("/models"):
            self._write(200, {"object": "list", "data": [{"id": "mock-model"}]})
        else:
            self._write(404, {"error": "not found"})

    def do_POST(self):
        type(self).last_headers = {k.lower(): v for k, v in self.headers.items()}
        size = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(size) or b"{}"
        if self.path.endswith("/oauth/token"):
            self._write(200, {"access_token": "mock-access-token", "refresh_token": "mock-refresh-token", "token_type": "Bearer", "expires_in": 3600})
            return
        if "application/x-www-form-urlencoded" in self.headers.get("Content-Type", ""):
            self._write(400, {"error": "unexpected form endpoint"})
            return
        body = json.loads(raw)
        model = body.get("model", "unknown")
        if self.path.endswith("/chat/completions"):
            if model == "fails":
                self._write(503, {"error": {"message": "mock provider unavailable"}})
                return
            if body.get("stream"):
                chunks = [
                    'data: {"id":"chatcmpl-mock","choices":[{"delta":{"content":"streamed "}}]}\n\n',
                    'data: {"id":"chatcmpl-mock","choices":[{"delta":{"content":"response"}}],"usage":{"prompt_tokens":2,"completion_tokens":2}}\n\n',
                    'data: [DONE]\n\n',
                ]
                raw = "".join(chunks).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            self._write(200, {"id": "chatcmpl-mock", "object": "chat.completion", "choices": [{"index": 0, "message": {"role": "assistant", "content": f"served-by-{model}"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 4, "completion_tokens": 5, "total_tokens": 9}})
        elif self.path.endswith("/responses"):
            self._write(200, {"id": "resp_mock", "object": "response", "model": model, "output": [{"type": "message", "content": [{"type": "output_text", "text": "response-ok"}]}]})
        elif self.path.endswith("/messages"):
            self._write(200, {"id": "msg_mock", "type": "message", "role": "assistant", "model": model, "content": [{"type": "text", "text": "anthropic-ok"}], "stop_reason": "end_turn", "usage": {"input_tokens": 3, "output_tokens": 2}})
        elif self.path.endswith("/embeddings"):
            self._write(200, {"object": "list", "data": [{"object": "embedding", "embedding": [0.1, 0.2, 0.3], "index": 0}], "model": model, "usage": {"prompt_tokens": 3, "total_tokens": 3}})
        elif self.path.endswith("/completions"):
            self._write(200, {"id": "cmpl_mock", "object": "text_completion", "model": model, "choices": [{"text": "completion-ok", "index": 0}], "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}})
        elif self.path.endswith("/images/generations"):
            self._write(200, {"created": 1, "data": [{"url": "https://example.invalid/mock.png"}]})
        elif self.path.endswith("/moderations"):
            self._write(200, {"id": "mod_mock", "results": [{"flagged": False}]})
        elif self.path.endswith("/audio/speech"):
            raw = b"MOSAICROUTE-MOCK-AUDIO"
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        else:
            self._write(404, {"error": {"message": "unexpected endpoint"}})


server = ThreadingHTTPServer(("127.0.0.1", 0), MockProvider)
server_thread = threading.Thread(target=server.serve_forever, daemon=True)
server_thread.start()
MOCK_BASE = f"http://127.0.0.1:{server.server_address[1]}/v1"


@pytest.fixture(autouse=True)
def reset_db():
    with mosaic.db() as conn:
        conn.execute("DELETE FROM request_logs")
        conn.execute("DELETE FROM model_routes")
        conn.execute("DELETE FROM providers")
    mosaic.SESSIONS.clear()
    yield


@pytest.fixture

def client():
    with TestClient(mosaic.app) as test_client:
        yield test_client


@pytest.fixture

def signed_client(client):
    response = client.post("/api/auth/login", json={"password": mosaic.ADMIN_PASSWORD})
    assert response.status_code == 200
    return client


def add_provider(client, name: str, protocol: str = "openai") -> int:
    response = client.post("/api/providers", json={
        "name": name,
        "base_url": MOCK_BASE,
        "protocol": protocol,
        "api_key": "unit-test-key",
        "is_active": True,
    })
    assert response.status_code == 200, response.text
    return response.json()["id"]


def add_provider_with_auth(client, name: str, *, auth_type: str, api_key: str = "", protocol: str = "openai", auth_header: str = "", auth_prefix: str = "", custom_headers=None, oauth_config=None) -> int:
    response = client.post("/api/providers", json={
        "name": name, "base_url": MOCK_BASE, "protocol": protocol, "auth_type": auth_type,
        "api_key": api_key, "auth_header": auth_header, "auth_prefix": auth_prefix,
        "custom_headers": custom_headers, "oauth_config": oauth_config or {}, "is_active": True,
    })
    assert response.status_code == 200, response.text
    return response.json()["id"]

def add_route(client, alias: str, candidates: list[dict]) -> None:
    response = client.post("/api/models", json={"alias": alias, "candidates": candidates})
    assert response.status_code == 200, response.text


def test_health_and_dashboard_requires_login(client):
    assert client.get("/health").json()["ok"] is True
    assert client.get("/api/providers").status_code == 401
    assert client.get("/").status_code == 200


def test_dashboard_assets_are_served_locally(client):
    page = client.get("/")
    css = client.get("/static/app.css")
    js = client.get("/static/app.js")
    assert page.status_code == 200
    assert '/static/app.css' in page.text and '/static/app.js' in page.text
    assert css.status_code == 200 and 'font-family' in css.text
    assert js.status_code == 200 and 'providerPresets' in js.text
    assert 'cdnjs.cloudflare.com' not in page.text


def test_login_rejects_wrong_password_and_sets_cookie(client):
    bad = client.post("/api/auth/login", json={"password": "incorrect"})
    assert bad.status_code == 401
    good = client.post("/api/auth/login", json={"password": mosaic.ADMIN_PASSWORD})
    assert good.status_code == 200
    assert mosaic.SESSION_COOKIE in good.headers.get("set-cookie", "")
    assert client.get("/api/bootstrap").status_code == 200


def test_provider_keys_are_never_returned_plaintext(signed_client):
    provider_id = add_provider(signed_client, "Mock")
    provider = signed_client.get("/api/providers").json()[0]
    assert provider["id"] == provider_id
    assert provider["api_key_set"] is True
    assert "api_key" not in provider
    with mosaic.db() as conn:
        saved = conn.execute("SELECT api_key_enc FROM providers WHERE id=?", (provider_id,)).fetchone()["api_key_enc"]
    assert saved != "unit-test-key"
    assert mosaic.decrypt_secret(saved) == "unit-test-key"


def test_chat_falls_back_after_503_and_logs_usage(signed_client):
    degraded_id = add_provider(signed_client, "Degraded")
    healthy_id = add_provider(signed_client, "Healthy")
    add_route(signed_client, "coding", [
        {"provider_id": degraded_id, "upstream_model": "fails"},
        {"provider_id": healthy_id, "upstream_model": "mock-model"},
    ])
    response = signed_client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {mosaic.LOCAL_API_KEY}"}, json={
        "model": "coding", "messages": [{"role": "user", "content": "hello"}], "stream": False,
    })
    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"] == "served-by-mock-model"
    logs = signed_client.get("/api/logs").json()
    assert logs[0]["upstream_provider"] == "Healthy"
    assert logs[0]["prompt_tokens"] == 4
    assert logs[0]["completion_tokens"] == 5
    with mosaic.db() as conn:
        degraded = conn.execute("SELECT cooldown_until FROM providers WHERE id=?", (degraded_id,)).fetchone()
    assert degraded["cooldown_until"] > 0


def test_streaming_chat_is_forwarded_and_logged(signed_client):
    provider_id = add_provider(signed_client, "Streaming")
    add_route(signed_client, "stream-test", [{"provider_id": provider_id, "upstream_model": "mock-model"}])
    response = signed_client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {mosaic.LOCAL_API_KEY}"}, json={
        "model": "stream-test", "messages": [{"role": "user", "content": "stream"}], "stream": True,
    })
    assert response.status_code == 200
    assert "streamed" in response.text and "[DONE]" in response.text
    logs = signed_client.get("/api/logs").json()
    assert logs[0]["prompt_tokens"] == 2
    assert logs[0]["completion_tokens"] == 2


def test_responses_pass_through(signed_client):
    provider_id = add_provider(signed_client, "Responses")
    add_route(signed_client, "responses-test", [{"provider_id": provider_id, "upstream_model": "mock-model"}])
    response = signed_client.post("/v1/responses", headers={"Authorization": f"Bearer {mosaic.LOCAL_API_KEY}"}, json={
        "model": "responses-test", "input": "hello",
    })
    assert response.status_code == 200
    assert response.json()["id"] == "resp_mock"
    assert response.json()["model"] == "mock-model"


def test_anthropic_messages_pass_through(signed_client):
    provider_id = add_provider(signed_client, "Anthropic mock", protocol="anthropic")
    add_route(signed_client, "claude-alias", [{"provider_id": provider_id, "upstream_model": "claude-mock"}])
    response = signed_client.post("/v1/messages", headers={"Authorization": f"Bearer {mosaic.LOCAL_API_KEY}"}, json={
        "model": "claude-alias", "max_tokens": 30, "messages": [{"role": "user", "content": "hello"}],
    })
    assert response.status_code == 200
    assert response.json()["content"][0]["text"] == "anthropic-ok"
    assert response.json()["model"] == "claude-mock"


def test_api_rejects_missing_key_and_unknown_alias(signed_client):
    assert signed_client.get("/v1/models").status_code == 401
    missing = signed_client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {mosaic.LOCAL_API_KEY}"}, json={
        "model": "missing", "messages": [],
    })
    assert missing.status_code == 404
    assert missing.json()["error"]["type"] == "not_found"


def test_cross_origin_dashboard_write_is_blocked(client):
    response = client.post("/api/auth/login", headers={"Origin": "http://evil.example"}, json={"password": mosaic.ADMIN_PASSWORD})
    assert response.status_code == 403


def test_alias_rejects_spaces(signed_client):
    provider_id = add_provider(signed_client, "Mock")
    response = signed_client.post("/api/models", json={"alias": "not valid", "candidates": [{"provider_id": provider_id, "upstream_model": "mock-model"}]})
    assert response.status_code == 400



def test_provider_catalog_is_searchable_and_local_first(signed_client):
    catalog = signed_client.get("/api/catalog/providers")
    assert catalog.status_code == 200
    providers = catalog.json()
    ids = {p["id"] for p in providers}
    assert {"openai", "anthropic", "openrouter", "nara", "ollama", "lmstudio", "deepinfra", "sambanova", "featherless", "pollinations", "moonshot", "custom"}.issubset(ids)
    assert len(providers) == 112
    assert len(ids) == len(providers), "provider template IDs must be unique"
    assert next(p for p in providers if p["id"] == "ollama")["auth_type"] == "none"
    assert all("free_tier_status" in p and "access_note" in p for p in providers)
    assert next(p for p in providers if p["id"] == "sambanova")["free_tier_status"] == "free-tier-retired"


def test_custom_api_key_header_is_sent_and_secret_is_not_exposed(signed_client):
    provider_id = add_provider_with_auth(signed_client, "Header key", auth_type="api_key", api_key="secret-key", auth_header="x-api-key")
    add_route(signed_client, "header-key", [{"provider_id": provider_id, "upstream_model": "mock-model"}])
    result = signed_client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {mosaic.LOCAL_API_KEY}"}, json={
        "model": "header-key", "messages": [{"role": "user", "content": "hi"}],
    })
    assert result.status_code == 200
    assert MockProvider.last_headers.get("x-api-key") == "secret-key"
    public = signed_client.get("/api/providers").json()[0]
    assert "secret-key" not in json.dumps(public)


def test_cookie_session_and_custom_headers_are_forwarded(signed_client):
    provider_id = add_provider_with_auth(signed_client, "Cookie endpoint", auth_type="cookie", api_key="session=local-test", custom_headers={"X-Client-Tag": "MosaicRoute"})
    add_route(signed_client, "cookie-route", [{"provider_id": provider_id, "upstream_model": "mock-model"}])
    result = signed_client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {mosaic.LOCAL_API_KEY}"}, json={
        "model": "cookie-route", "messages": [{"role": "user", "content": "hi"}],
    })
    assert result.status_code == 200
    assert MockProvider.last_headers.get("cookie") == "session=local-test"
    assert MockProvider.last_headers.get("x-client-tag") == "MosaicRoute"


def test_session_token_with_custom_header_prefix(signed_client):
    provider_id = add_provider_with_auth(signed_client, "Session header", auth_type="session", api_key="opaque-session", auth_header="X-Session-Token", auth_prefix="Token ")
    add_route(signed_client, "session-route", [{"provider_id": provider_id, "upstream_model": "mock-model"}])
    result = signed_client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {mosaic.LOCAL_API_KEY}"}, json={
        "model": "session-route", "messages": [{"role": "user", "content": "hi"}],
    })
    assert result.status_code == 200
    assert MockProvider.last_headers.get("x-session-token") == "Token opaque-session"


def test_oauth_pkce_flow_stores_tokens_encrypted_and_uses_access_token(signed_client):
    oauth_config = {
        "authorization_url": MOCK_BASE + "/oauth/authorize", "token_url": MOCK_BASE + "/oauth/token",
        "client_id": "test-client", "client_secret": "", "scopes": "chat", "token_auth_method": "none",
    }
    provider_id = add_provider_with_auth(signed_client, "OAuth mock", auth_type="oauth2", oauth_config=oauth_config)
    start = signed_client.post(f"/api/providers/{provider_id}/oauth/start", json={})
    assert start.status_code == 200, start.text
    authorization_url = start.json()["authorization_url"]
    from urllib.parse import parse_qs, urlsplit
    query = parse_qs(urlsplit(authorization_url).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"]
    state = query["state"][0]
    callback = signed_client.get("/api/oauth/callback", params={"code": "mock-code", "state": state})
    assert callback.status_code == 200
    assert "Provider connected" in callback.text
    assert "Model discovery found 1 model(s)" in callback.text
    with mosaic.db() as conn:
        stored = conn.execute("SELECT api_key_enc FROM providers WHERE id=?", (provider_id,)).fetchone()["api_key_enc"]
    assert "mock-access-token" not in stored
    assert json.loads(mosaic.decrypt_secret(stored))["access_token"] == "mock-access-token"
    add_route(signed_client, "oauth-route", [{"provider_id": provider_id, "upstream_model": "mock-model"}])
    result = signed_client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {mosaic.LOCAL_API_KEY}"}, json={
        "model": "oauth-route", "messages": [{"role": "user", "content": "hi"}],
    })
    assert result.status_code == 200
    assert MockProvider.last_headers.get("authorization") == "Bearer mock-access-token"



def test_balanced_strategy_learns_from_provider_health_metrics(signed_client):
    unreliable = add_provider(signed_client, "Fast but unreliable")
    reliable = add_provider(signed_client, "Reliable provider")
    add_route(signed_client, "auto-balanced", [
        {"provider_id": unreliable, "upstream_model": "fast-model"},
        {"provider_id": reliable, "upstream_model": "reliable-model"},
    ])
    with mosaic.db() as conn:
        conn.execute("INSERT INTO provider_metrics(provider_id,successes,failures,avg_latency_ms,last_status,updated_at) VALUES(?,?,?,?,?,?)", (unreliable, 3, 7, 80, 503, mosaic.utc_now()))
        conn.execute("INSERT INTO provider_metrics(provider_id,successes,failures,avg_latency_ms,last_status,updated_at) VALUES(?,?,?,?,?,?)", (reliable, 18, 2, 400, 200, mosaic.utc_now()))
    candidates = mosaic.resolve_candidates("auto-balanced", "openai")
    assert candidates[0]["id"] == reliable


def test_remote_plain_http_provider_url_is_rejected(signed_client):
    response = signed_client.post("/api/providers", json={"name": "Insecure remote", "base_url": "http://example.com/v1", "protocol": "openai", "auth_type": "auto", "is_active": True})
    assert response.status_code == 400

def test_rejects_header_injection_and_invalid_oauth_urls(signed_client):
    invalid = signed_client.post("/api/providers", json={
        "name": "Bad header", "base_url": MOCK_BASE, "protocol": "openai", "auth_type": "custom_header",
        "auth_header": "X-Test\r\nX-Evil", "auth_prefix": "", "api_key": "value", "is_active": True,
    })
    assert invalid.status_code == 400
    bad_oauth = signed_client.post("/api/providers", json={
        "name": "Bad OAuth", "base_url": MOCK_BASE, "protocol": "openai", "auth_type": "oauth2",
        "oauth_config": {"authorization_url": "http://not-local.example/auth", "token_url": MOCK_BASE + "/oauth/token", "client_id": "client"},
        "is_active": True,
    })
    assert bad_oauth.status_code == 400

def teardown_module(_module):
    server.shutdown()
    server.server_close()


def test_balanced_strategy_explores_an_unmeasured_provider(signed_client):
    known = add_provider(signed_client, "Known route")
    unseen = add_provider(signed_client, "Unseen route")
    add_route(signed_client, "auto-explore", [
        {"provider_id": known, "upstream_model": "known-model"},
        {"provider_id": unseen, "upstream_model": "unseen-model"},
    ])
    with mosaic.db() as conn:
        conn.execute(
            "INSERT INTO provider_metrics(provider_id,successes,failures,avg_latency_ms,last_status,updated_at) VALUES(?,?,?,?,?,?)",
            (known, 20, 0, 100, 200, mosaic.utc_now()),
        )
    candidates = mosaic.resolve_candidates("auto-explore", "openai")
    assert candidates[0]["id"] == unseen


def test_custom_headers_reject_control_characters(signed_client):
    response = signed_client.post("/api/providers", json={
        "name": "Bad header value", "base_url": MOCK_BASE, "protocol": "openai",
        "auth_type": "custom_headers", "custom_headers": {"X-Test": "safe\r\nX-Injected: yes"},
        "is_active": True,
    })
    assert response.status_code == 400


def test_normalize_discovered_models_handles_multiple_shapes_and_cost_hints():
    rows = mosaic.normalize_discovered_models({"data": [
        {"id": "router/model:free", "pricing": {"prompt": "0", "completion": "0"}, "context_length": 8192},
        {"id": "paid-model", "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
        {"id": "unknown-cost"},
        {"id": "router/model:free"},
        None,
    ]})
    by_id = {row["model_id"]: row for row in rows}
    assert len(rows) == 3
    assert by_id["router/model:free"]["free_status"] == "provider-priced-zero"
    assert by_id["router/model:free"]["metadata"]["context_length"] == 8192
    assert by_id["paid-model"]["free_status"] == "unknown"
    assert by_id["unknown-cost"]["free_status"] == "unknown"


def test_discovery_persists_catalog_and_discovered_models_are_directly_callable(signed_client):
    provider_id = add_provider(signed_client, "Discoverable")
    discovery = signed_client.post(f"/api/providers/{provider_id}/discover", json={})
    assert discovery.status_code == 200, discovery.text
    result = discovery.json()
    assert result["ok"] and result["count"] >= 1
    catalog = signed_client.get("/api/discovery/models").json()
    assert any(item["provider_id"] == provider_id and item["model_id"] == "mock-model" for item in catalog)
    # No alias required: discovered upstream model IDs are directly routable.
    response = signed_client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {mosaic.LOCAL_API_KEY}"}, json={
        "model": "mock-model", "messages": [{"role": "user", "content": "discovery test"}],
    })
    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"] == "served-by-mock-model"
    models = signed_client.get("/v1/models", headers={"Authorization": f"Bearer {mosaic.LOCAL_API_KEY}"}).json()["data"]
    assert any(item["id"] == "mock-model" for item in models)


def test_discovery_builds_auto_routes_without_claiming_unknown_models_are_free(signed_client):
    provider_id = add_provider(signed_client, "Discoverable")
    signed_client.post(f"/api/providers/{provider_id}/discover", json={})
    built = signed_client.post("/api/discovery/create-auto-routes", json={})
    assert built.status_code == 200, built.text
    result = built.json()
    assert any(row["alias"] == "auto" for row in result["created_or_updated"])
    assert any(row["alias"] == "auto/free" for row in result["skipped"])
    routes = signed_client.get("/api/models").json()
    assert any(route["alias"] == "auto" for route in routes)
    assert not any(route["alias"] == "auto/free" for route in routes)


def test_discovery_root_path_mode_and_custom_model_path_are_persisted(signed_client):
    created = signed_client.post("/api/providers", json={
        "name": "Host Root Models", "base_url": MOCK_BASE, "models_path": "/models",
        "models_path_mode": "host", "protocol": "openai", "auth_type": "none", "is_active": True,
    })
    assert created.status_code == 200, created.text
    provider_id = created.json()["id"]
    assert created.json()["models_path_mode"] == "host"
    result = signed_client.post(f"/api/providers/{provider_id}/discover", json={})
    assert result.status_code == 200 and result.json()["ok"]
    assert MockProvider.last_path == "/models", "host mode must request the path from the origin root"
    bad = signed_client.post("/api/providers", json={
        "name": "Invalid Model Path", "base_url": MOCK_BASE, "models_path": "/../admin",
        "protocol": "openai", "auth_type": "none", "is_active": True,
    })
    assert bad.status_code == 400


def test_model_discovery_url_respects_prefix_and_host_root_modes():
    assert mosaic.model_discovery_url({"base_url": "https://example.test/v1", "models_path": "/models", "models_path_mode": "base"}) == "https://example.test/v1/models"
    assert mosaic.model_discovery_url({"base_url": "https://example.test/v1", "models_path": "/api/models", "models_path_mode": "host"}) == "https://example.test/api/models"


def test_discovery_refresh_reports_provider_and_model_counts(signed_client):
    add_provider(signed_client, "Refresh one")
    add_provider(signed_client, "Refresh two")
    response = signed_client.post("/api/discovery/refresh", json={})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["providers_checked"] == 2
    assert result["providers_succeeded"] == 2
    assert result["models_available"] >= 1


def test_cookie_auth_is_used_for_model_discovery(signed_client):
    provider_id = add_provider_with_auth(signed_client, "Cookie discovery", auth_type="cookie", api_key="session_id=authorized-manual", custom_headers={"X-Catalog-Client": "MosaicRoute-test"})
    response = signed_client.post(f"/api/providers/{provider_id}/discover", json={})
    assert response.status_code == 200 and response.json()["ok"]
    assert MockProvider.last_headers.get("cookie") == "session_id=authorized-manual"
    assert MockProvider.last_headers.get("x-catalog-client") == "MosaicRoute-test"


def test_explicit_free_route_path_is_recognized_as_hint():
    rows = mosaic.normalize_discovered_models({"data": [{"id": "orcarouter/free"}, {"id": "provider/model:free"}, {"id": "regular-model"}]})
    statuses = {row["model_id"]: row["free_status"] for row in rows}
    assert statuses["orcarouter/free"] == "explicit-free-model-id-hint"
    assert statuses["provider/model:free"] == "explicit-free-model-id-hint"
    assert statuses["regular-model"] == "unknown"


def test_auto_route_generation_never_overwrites_manual_alias(signed_client):
    provider_id = add_provider(signed_client, "Manual alias provider")
    add_route(signed_client, "auto", [{"provider_id": provider_id, "upstream_model": "manual-model"}])
    # Discovery gives the system enough metadata to build its default aliases.
    discovered = signed_client.post(f"/api/providers/{provider_id}/discover", json={})
    assert discovered.status_code == 200 and discovered.json()["ok"]
    with mosaic.db() as conn:
        route = conn.execute("SELECT candidates_json,is_auto_generated FROM model_routes WHERE alias='auto'").fetchone()
    assert route["is_auto_generated"] == 0
    assert json.loads(route["candidates_json"])[0]["upstream_model"] == "manual-model"


def test_empty_catalog_cleans_only_generated_auto_routes(signed_client):
    provider_id = add_provider(signed_client, "Stale catalog provider")
    with mosaic.db() as conn:
        now = mosaic.utc_now()
        conn.execute("INSERT INTO discovered_models(provider_id,model_id,display_name,metadata_json,free_status,is_available,discovered_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?)",
                     (provider_id, "model:free", "model:free", "{}", "explicit-free-model-id-hint", 1, now, now))
    built = signed_client.post("/api/discovery/create-auto-routes", json={})
    assert built.status_code == 200
    with mosaic.db() as conn:
        generated = conn.execute("SELECT is_auto_generated FROM model_routes WHERE alias='auto/free'").fetchone()
    assert generated and generated["is_auto_generated"] == 1
    add_route(signed_client, "manual-route", [{"provider_id": provider_id, "upstream_model": "manual-model"}])
    with mosaic.db() as conn:
        conn.execute("UPDATE discovered_models SET is_available=0")
    refreshed = signed_client.post("/api/discovery/create-auto-routes", json={})
    assert refreshed.status_code == 200
    routes = {item["alias"] for item in signed_client.get("/api/models").json()}
    assert "manual-route" in routes
    assert "auto/free" not in routes


def test_standalone_model_discovery_parser_handles_list_payload():
    from mosaicroute_discovery import ModelDiscovery, normalize_model_catalog
    models = normalize_model_catalog(["plain-model", {"id": "provider/model:free", "context_length": 64000}], "provider")
    assert [model.id for model in models] == ["plain-model", "provider/model:free"]
    assert models[1].context_window == 64000
    assert isinstance(ModelDiscovery().rank(models, "general"), list)


def test_empty_or_unknown_model_list_response_preserves_last_good_catalog(signed_client):
    provider_response = signed_client.post("/api/providers", json={
        "name": "Empty-list provider", "base_url": MOCK_BASE, "models_path": "/models",
        "protocol": "openai", "auth_type": "none", "is_active": True,
    })
    provider_id = provider_response.json()["id"]
    good = signed_client.post(f"/api/providers/{provider_id}/discover", json={})
    assert good.status_code == 200 and good.json()["ok"]
    updated = signed_client.put(f"/api/providers/{provider_id}", json={
        "name": "Empty-list provider", "base_url": MOCK_BASE, "models_path": "/empty/models",
        "protocol": "openai", "auth_type": "none", "is_active": True,
    })
    assert updated.status_code == 200
    bad = signed_client.post(f"/api/providers/{provider_id}/discover", json={})
    assert bad.status_code == 200 and not bad.json()["ok"]
    catalog = signed_client.get("/api/discovery/models").json()
    assert any(item["provider_id"] == provider_id and item["model_id"] == "mock-model" for item in catalog)


def test_provider_quota_limits_are_persisted_and_enforced(signed_client):
    response = signed_client.post("/api/providers", json={
        "name": "Capped", "base_url": MOCK_BASE, "protocol": "openai", "auth_type": "bearer",
        "api_key": "quota-test-key", "is_active": True, "rpm_limit": 1, "rpd_limit": 10,
        "tpm_limit": 20000, "tpd_limit": 100000, "max_concurrency": 2,
    })
    assert response.status_code == 200, response.text
    provider_id = response.json()["id"]
    assert response.json()["rate_limits"]["rpm_limit"] == 1
    add_route(signed_client, "capped", [{"provider_id": provider_id, "upstream_model": "mock-model"}])
    headers = {"Authorization": f"Bearer {mosaic.LOCAL_API_KEY}"}
    body = {"model": "capped", "messages": [{"role": "user", "content": "hello"}]}
    first = signed_client.post("/v1/chat/completions", headers=headers, json=body)
    second = signed_client.post("/v1/chat/completions", headers=headers, json=body)
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["type"] == "rate_limit_exceeded"
    quota = signed_client.get(f"/api/providers/{provider_id}/quota").json()
    assert quota["last_minute"]["requests"] == 1
    assert quota["today_utc"]["requests"] == 1
    assert quota["in_flight"] == 0
    with mosaic.db() as conn:
        usage = conn.execute("SELECT actual_tokens,status_code FROM quota_events WHERE provider_id=? ORDER BY id LIMIT 1", (provider_id,)).fetchone()
    assert usage["actual_tokens"] == 9
    assert usage["status_code"] == 200


def test_unlimited_provider_does_not_write_quota_events(signed_client):
    provider_id = add_provider(signed_client, "Unlimited")
    add_route(signed_client, "unlimited", [{"provider_id": provider_id, "upstream_model": "mock-model"}])
    response = signed_client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {mosaic.LOCAL_API_KEY}"}, json={
        "model": "unlimited", "messages": [{"role": "user", "content": "hello"}],
    })
    assert response.status_code == 200
    with mosaic.db() as conn:
        count = conn.execute("SELECT COUNT(*) n FROM quota_events WHERE provider_id=?", (provider_id,)).fetchone()["n"]
    assert count == 0


def test_embeddings_endpoint_is_openai_compatible_and_logs_usage(signed_client):
    provider_id = add_provider(signed_client, "Embeddings")
    add_route(signed_client, "embed-model", [{"provider_id": provider_id, "upstream_model": "embed-mock"}])
    response = signed_client.post("/v1/embeddings", headers={"Authorization": f"Bearer {mosaic.LOCAL_API_KEY}"}, json={
        "model": "embed-model", "input": ["hello", "world"],
    })
    assert response.status_code == 200, response.text
    assert response.json()["data"][0]["embedding"] == [0.1, 0.2, 0.3]
    logs = signed_client.get("/api/logs").json()
    assert logs[0]["upstream_model"] == "embed-mock"
    assert logs[0]["prompt_tokens"] == 3


def test_embeddings_do_not_fail_over_across_unknown_vector_spaces(signed_client):
    provider_id = add_provider(signed_client, "Embed primary")
    second_id = add_provider(signed_client, "Embed second")
    candidates = [
        {"id": provider_id, "upstream_model": "same-id", "model_metadata": {}},
        {"id": second_id, "upstream_model": "same-id", "model_metadata": {}},
        {"id": second_id, "upstream_model": "different-id", "model_metadata": {"dimensions": 3}},
    ]
    assert mosaic.safe_embedding_candidates(candidates) == [candidates[0]]
    candidates[0]["model_metadata"] = {"dimensions": 3}
    candidates[1]["model_metadata"] = {"dimensions": 3}
    assert mosaic.safe_embedding_candidates(candidates) == candidates[:2]


def test_legacy_completions_images_and_moderations_are_forwarded(signed_client):
    provider_id = add_provider(signed_client, "Other OpenAI endpoints")
    add_route(signed_client, "compatible", [{"provider_id": provider_id, "upstream_model": "mock-model"}])
    headers = {"Authorization": f"Bearer {mosaic.LOCAL_API_KEY}"}
    completion = signed_client.post("/v1/completions", headers=headers, json={"model": "compatible", "prompt": "hello"})
    image = signed_client.post("/v1/images/generations", headers=headers, json={"model": "compatible", "prompt": "a square"})
    moderation = signed_client.post("/v1/moderations", headers=headers, json={"model": "compatible", "input": "hello"})
    assert completion.status_code == 200 and completion.json()["choices"][0]["text"] == "completion-ok"
    assert image.status_code == 200 and image.json()["data"][0]["url"].endswith("mock.png")
    assert moderation.status_code == 200 and moderation.json()["results"][0]["flagged"] is False


def test_audio_speech_binary_response_is_not_parsed_as_json(signed_client):
    provider_id = add_provider(signed_client, "Speech")
    add_route(signed_client, "voice", [{"provider_id": provider_id, "upstream_model": "tts-mock"}])
    response = signed_client.post("/v1/audio/speech", headers={"Authorization": f"Bearer {mosaic.LOCAL_API_KEY}"}, json={
        "model": "voice", "input": "hello", "voice": "alloy",
    })
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.content == b"MOSAICROUTE-MOCK-AUDIO"


def test_round_robin_strategy_rotates_candidates(signed_client):
    first_id = add_provider(signed_client, "Round one")
    second_id = add_provider(signed_client, "Round two")
    response = signed_client.post("/api/models", json={"alias": "rotate", "strategy": "round_robin", "candidates": [
        {"provider_id": first_id, "upstream_model": "one"},
        {"provider_id": second_id, "upstream_model": "two"},
    ]})
    assert response.status_code == 200, response.text
    first = mosaic.resolve_candidates("rotate", "openai")
    second = mosaic.resolve_candidates("rotate", "openai")
    assert first[0]["id"] != second[0]["id"]


def test_model_metrics_are_recorded_separately_from_provider_metrics(signed_client):
    provider_id = add_provider(signed_client, "Model metrics")
    mosaic.note_provider_result(provider_id, True, 200, 50, "model-a")
    mosaic.note_provider_result(provider_id, False, 503, 150, "model-b")
    with mosaic.db() as conn:
        a = conn.execute("SELECT successes,failures FROM provider_model_metrics WHERE provider_id=? AND model_id='model-a'", (provider_id,)).fetchone()
        b = conn.execute("SELECT successes,failures FROM provider_model_metrics WHERE provider_id=? AND model_id='model-b'", (provider_id,)).fetchone()
    assert (a["successes"], a["failures"]) == (1, 0)
    assert (b["successes"], b["failures"]) == (0, 1)


def test_retry_after_parser_and_cooldown_respect_upstream_backoff(signed_client):
    assert mosaic.parse_retry_after("120") == 120
    assert mosaic.parse_retry_after("not-a-delay") is None
    provider_id = add_provider(signed_client, "Backoff")
    mosaic.note_provider_result(provider_id, False, 429, 10, "model-a", 120)
    with mosaic.db() as conn:
        cooldown_until = conn.execute("SELECT cooldown_until FROM providers WHERE id=?", (provider_id,)).fetchone()["cooldown_until"]
    assert cooldown_until >= __import__("time").time() + 110


def test_sticky_sessions_keep_chat_on_the_same_provider_and_only_store_a_hash(signed_client):
    one = add_provider(signed_client, "Sticky One")
    two = add_provider(signed_client, "Sticky Two")
    route = signed_client.post("/api/models", json={"alias": "sticky", "strategy": "round_robin", "candidates": [
        {"provider_id": one, "upstream_model": "model-one"},
        {"provider_id": two, "upstream_model": "model-two"},
    ]})
    assert route.status_code == 200, route.text
    headers = {"Authorization": f"Bearer {mosaic.LOCAL_API_KEY}", "X-MosaicRoute-Session": "private-conversation-123"}
    body = {"model": "sticky", "messages": [{"role": "user", "content": "hello"}]}
    first = signed_client.post("/v1/chat/completions", headers=headers, json=body)
    second = signed_client.post("/v1/chat/completions", headers=headers, json=body)
    assert first.status_code == 200 and second.status_code == 200
    assert first.headers["x-mosaicroute-provider"] == second.headers["x-mosaicroute-provider"]
    with mosaic.db() as conn:
        rows = conn.execute("SELECT session_hash,provider_id,model_id FROM sticky_sessions").fetchall()
    assert len(rows) == 1
    assert rows[0]["session_hash"] != "private-conversation-123"
    assert len(rows[0]["session_hash"]) == 64


def test_session_identity_is_not_used_for_embedding_or_unbounded_values(signed_client):
    class HeaderStub:
        headers = {"x-mosaicroute-session": "x" * 513}
    assert mosaic.get_sticky_session_key(HeaderStub(), {"user": "abc"}, "model", "openai") == ""
    class UserStub:
        headers = {}
    assert mosaic.get_sticky_session_key(UserStub(), {"user": "abc"}, "model", "openai")


def test_analytics_endpoint_reports_percentiles_and_provider_breakdown(signed_client):
    provider_id = add_provider(signed_client, "Analytics provider")
    add_route(signed_client, "analytics-model", [{"provider_id": provider_id, "upstream_model": "mock-model"}])
    headers = {"Authorization": f"Bearer {mosaic.LOCAL_API_KEY}"}
    good = signed_client.post("/v1/chat/completions", headers=headers, json={
        "model": "analytics-model", "messages": [{"role": "user", "content": "one"}],
    })
    assert good.status_code == 200
    analytics = signed_client.get("/api/analytics?window_hours=24")
    assert analytics.status_code == 200, analytics.text
    result = analytics.json()
    assert result["requests"] >= 1
    assert result["successes"] >= 1
    assert result["latency_ms"]["p50"] >= 0
    assert result["latency_ms"]["p95"] >= result["latency_ms"]["p50"]
    assert any(row["name"] == "Analytics provider" for row in result["providers"])


def test_analytics_window_is_bounded(signed_client):
    data = signed_client.get("/api/analytics?window_hours=999999")
    assert data.status_code == 200
    assert data.json()["window_hours"] == 24 * 90



def test_security_headers_and_private_api_cache_control(client):
    page = client.get("/")
    assert page.headers.get("content-security-policy", "").startswith("default-src 'self'")
    assert page.headers["cache-control"] == "no-store"
    assert page.headers.get("x-content-type-options") == "nosniff"
    status = client.get("/api/auth/status")
    assert status.headers.get("cache-control") == "no-store"
    assert status.headers.get("permissions-policy")


def test_json_provider_catalog_is_single_source_of_truth(signed_client):
    from pathlib import Path
    payload = json.loads((Path(mosaic.BASE_DIR) / "config" / "provider_catalog.json").read_text(encoding="utf-8"))
    api_catalog = signed_client.get("/api/catalog/providers")
    assert api_catalog.status_code == 200
    assert len(api_catalog.json()) == len(payload["providers"]) == payload["provider_preset_count"]
    assert {item["id"] for item in api_catalog.json()} == {item["id"] for item in payload["providers"]}
    assert payload["source_of_truth"].startswith("config/provider_catalog.json")



def test_model_discovery_follows_bounded_same_origin_pagination(signed_client):
    created = signed_client.post("/api/providers", json={
        "name": "Paginated Models", "base_url": MOCK_BASE, "models_path": "/paged/models",
        "protocol": "openai", "auth_type": "none", "is_active": True,
    })
    assert created.status_code == 200, created.text
    provider_id = created.json()["id"]
    response = signed_client.post(f"/api/providers/{provider_id}/discover", json={})
    assert response.status_code == 200 and response.json()["ok"]
    assert response.json()["pages"] == 2
    assert response.json()["count"] == 2
    assert MockProvider.last_path == "/v1/paged/models?page=2"
    rows = signed_client.get(f"/api/discovery/models?provider_id={provider_id}").json()
    assert {row["model_id"] for row in rows} == {"page-model-one", "page-model-two"}


def test_discovery_rejects_cross_origin_pagination_without_replacing_catalog(signed_client):
    created = signed_client.post("/api/providers", json={
        "name": "Unsafe Pagination", "base_url": MOCK_BASE, "models_path": "/external-next/models",
        "protocol": "openai", "auth_type": "none", "is_active": True,
    })
    provider_id = created.json()["id"]
    response = signed_client.post(f"/api/providers/{provider_id}/discover", json={})
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "cross-origin" in response.json()["message"]
    assert MockProvider.last_path == "/v1/external-next/models", "MosaicRoute must not follow a foreign pagination URL"
    rows = signed_client.get(f"/api/discovery/models?provider_id={provider_id}").json()
    assert rows == []


def test_discovery_page_cap_is_configurable_and_preserves_catalog(signed_client, monkeypatch):
    created = signed_client.post("/api/providers", json={
        "name": "Capped pagination", "base_url": MOCK_BASE, "models_path": "/paged/models",
        "protocol": "openai", "auth_type": "none", "is_active": True,
    })
    provider_id = created.json()["id"]
    monkeypatch.setenv("MOSAIC_DISCOVERY_MAX_PAGES", "1")
    response = signed_client.post(f"/api/providers/{provider_id}/discover", json={})
    assert response.status_code == 200 and response.json()["ok"] is False
    assert response.json()["status"] == 413
    assert response.json()["pages"] == 1
    assert signed_client.get(f"/api/discovery/models?provider_id={provider_id}").json() == []
