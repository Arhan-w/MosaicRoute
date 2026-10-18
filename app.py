from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

APP_NAME = "MosaicRoute"
APP_VERSION = "0.6.0"
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("MOSAIC_DATA_DIR", BASE_DIR / "data")).expanduser().resolve()
STATIC_DIR = BASE_DIR / "static"
ENV_PATH = Path(os.getenv("MOSAIC_ENV_FILE", BASE_DIR / ".env")).expanduser().resolve()
DB_PATH = DATA_DIR / "mosaicroute.sqlite3"
KEY_PATH = DATA_DIR / ".encryption_key"
SESSION_COOKIE = "mosaicroute_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
MAX_BODY_BYTES = 16 * 1024 * 1024
TRANSIENT_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}
SESSIONS: dict[str, float] = {}
OAUTH_PENDING: dict[str, dict[str, Any]] = {}

DATA_DIR.mkdir(parents=True, exist_ok=True)


def write_private_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows ACLs, rather than POSIX mode bits, govern Windows access.
        pass


def ensure_env() -> bool:
    """Create local credentials once; returns True only on first creation."""
    if ENV_PATH.exists():
        return False
    admin = os.getenv("MOSAIC_ADMIN_PASSWORD") or secrets.token_urlsafe(16)
    api_key = os.getenv("MOSAIC_LOCAL_API_KEY") or ("mr-local-" + secrets.token_urlsafe(32))
    lines = [
        "# Local-only credentials. Never commit this file.",
        f"MOSAIC_ADMIN_PASSWORD={admin}",
        f"MOSAIC_LOCAL_API_KEY={api_key}",
        "MOSAIC_HOST=127.0.0.1",
        "MOSAIC_PORT=8000",
    ]
    write_private_file(ENV_PATH, "\n".join(lines) + "\n")
    return True


FIRST_RUN = ensure_env()
load_dotenv(ENV_PATH, override=False)
ADMIN_PASSWORD = os.getenv("MOSAIC_ADMIN_PASSWORD", "").strip()
LOCAL_API_KEY = os.getenv("MOSAIC_LOCAL_API_KEY", "").strip()
if len(ADMIN_PASSWORD) < 12:
    raise RuntimeError("MOSAIC_ADMIN_PASSWORD must be at least 12 characters. Set it in .env and restart.")
if len(LOCAL_API_KEY) < 24:
    raise RuntimeError("MOSAIC_LOCAL_API_KEY must be at least 24 characters. Set it in .env and restart.")


def get_cipher() -> Fernet:
    if not KEY_PATH.exists():
        write_private_file(KEY_PATH, Fernet.generate_key().decode("ascii"))
    try:
        return Fernet(KEY_PATH.read_bytes().strip())
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Invalid data/.encryption_key. Restore the original key or reconfigure stored provider keys.") from exc


CIPHER = get_cipher()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS providers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                base_url TEXT NOT NULL,
                models_path TEXT NOT NULL DEFAULT '/models',
                models_path_mode TEXT NOT NULL DEFAULT 'base',
                api_key_enc TEXT NOT NULL DEFAULT '',
                protocol TEXT NOT NULL DEFAULT 'openai' CHECK(protocol IN ('openai','anthropic')),
                auth_type TEXT NOT NULL DEFAULT 'auto',
                auth_header TEXT NOT NULL DEFAULT '',
                auth_prefix TEXT NOT NULL DEFAULT '',
                custom_headers_enc TEXT NOT NULL DEFAULT '',
                oauth_config_enc TEXT NOT NULL DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 1,
                failure_count INTEGER NOT NULL DEFAULT 0,
                cooldown_until REAL NOT NULL DEFAULT 0,
                rpm_limit INTEGER NOT NULL DEFAULT 0,
                rpd_limit INTEGER NOT NULL DEFAULT 0,
                tpm_limit INTEGER NOT NULL DEFAULT 0,
                tpd_limit INTEGER NOT NULL DEFAULT 0,
                max_concurrency INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS model_routes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alias TEXT NOT NULL UNIQUE,
                candidates_json TEXT NOT NULL,
                strategy TEXT NOT NULL DEFAULT 'balanced',
                is_auto_generated INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS request_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                model_alias TEXT NOT NULL,
                upstream_provider TEXT NOT NULL DEFAULT '',
                upstream_model TEXT NOT NULL DEFAULT '',
                latency_ms INTEGER NOT NULL DEFAULT 0,
                status_code INTEGER NOT NULL DEFAULT 0,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                error_text TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_logs_created_at ON request_logs(created_at);
            CREATE TABLE IF NOT EXISTS discovered_models (
                provider_id INTEGER NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
                model_id TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                free_status TEXT NOT NULL DEFAULT 'unknown',
                is_available INTEGER NOT NULL DEFAULT 1,
                discovered_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY(provider_id, model_id)
            );
            CREATE INDEX IF NOT EXISTS idx_discovered_model_id ON discovered_models(model_id);
            CREATE INDEX IF NOT EXISTS idx_discovered_available ON discovered_models(is_available, provider_id);
            CREATE TABLE IF NOT EXISTS provider_model_metrics (
                provider_id INTEGER NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
                model_id TEXT NOT NULL,
                successes INTEGER NOT NULL DEFAULT 0,
                failures INTEGER NOT NULL DEFAULT 0,
                avg_latency_ms REAL NOT NULL DEFAULT 0,
                last_status INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(provider_id, model_id)
            );
            CREATE INDEX IF NOT EXISTS idx_provider_model_metrics_updated ON provider_model_metrics(updated_at);
            CREATE TABLE IF NOT EXISTS quota_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id INTEGER NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
                model_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                reserved_tokens INTEGER NOT NULL DEFAULT 0,
                actual_tokens INTEGER,
                status_code INTEGER NOT NULL DEFAULT 0,
                finished_at REAL,
                lease_until REAL NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_quota_provider_time ON quota_events(provider_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_quota_model_time ON quota_events(provider_id, model_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_quota_inflight ON quota_events(provider_id, lease_until, finished_at);
            CREATE TABLE IF NOT EXISTS sticky_sessions (
                session_hash TEXT NOT NULL,
                alias TEXT NOT NULL,
                protocol TEXT NOT NULL,
                provider_id INTEGER NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
                model_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at REAL NOT NULL,
                PRIMARY KEY(session_hash, alias, protocol)
            );
            CREATE INDEX IF NOT EXISTS idx_sticky_expires ON sticky_sessions(expires_at);
            CREATE TABLE IF NOT EXISTS provider_metrics (
                provider_id INTEGER PRIMARY KEY REFERENCES providers(id) ON DELETE CASCADE,
                successes INTEGER NOT NULL DEFAULT 0,
                failures INTEGER NOT NULL DEFAULT 0,
                avg_latency_ms REAL NOT NULL DEFAULT 0,
                last_status INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            """
        )
        # Upgrade databases created by early prototypes without dropping user data.
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(providers)").fetchall()}
        if "protocol" not in cols:
            conn.execute("ALTER TABLE providers ADD COLUMN protocol TEXT NOT NULL DEFAULT 'openai'")
        if "models_path" not in cols:
            conn.execute("ALTER TABLE providers ADD COLUMN models_path TEXT NOT NULL DEFAULT '/models'")
        if "models_path_mode" not in cols:
            conn.execute("ALTER TABLE providers ADD COLUMN models_path_mode TEXT NOT NULL DEFAULT 'base'")
        if "failure_count" not in cols:
            conn.execute("ALTER TABLE providers ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0")
        if "cooldown_until" not in cols:
            conn.execute("ALTER TABLE providers ADD COLUMN cooldown_until REAL NOT NULL DEFAULT 0")
        for name, definition in {
            "rpm_limit": "INTEGER NOT NULL DEFAULT 0",
            "rpd_limit": "INTEGER NOT NULL DEFAULT 0",
            "tpm_limit": "INTEGER NOT NULL DEFAULT 0",
            "tpd_limit": "INTEGER NOT NULL DEFAULT 0",
            "max_concurrency": "INTEGER NOT NULL DEFAULT 0",
            "auth_type": "TEXT NOT NULL DEFAULT 'auto'",
            "auth_header": "TEXT NOT NULL DEFAULT ''",
            "auth_prefix": "TEXT NOT NULL DEFAULT ''",
            "custom_headers_enc": "TEXT NOT NULL DEFAULT ''",
            "oauth_config_enc": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if name not in cols:
                conn.execute(f"ALTER TABLE providers ADD COLUMN {name} {definition}")
        route_cols = {row["name"] for row in conn.execute("PRAGMA table_info(model_routes)").fetchall()}
        if "strategy" not in route_cols:
            conn.execute("ALTER TABLE model_routes ADD COLUMN strategy TEXT NOT NULL DEFAULT 'balanced'")
        if "is_auto_generated" not in route_cols:
            conn.execute("ALTER TABLE model_routes ADD COLUMN is_auto_generated INTEGER NOT NULL DEFAULT 0")


init_db()


def encrypt_secret(secret: str) -> str:
    return CIPHER.encrypt(secret.encode("utf-8")).decode("ascii") if secret else ""


def decrypt_secret(secret: str) -> str:
    if not secret:
        return ""
    try:
        return CIPHER.decrypt(secret.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise RuntimeError("Could not decrypt a provider key. Keep data/.encryption_key with the database.") from exc


def verify_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        return
    try:
        origin_parts = urlsplit(origin)
    except ValueError:
        raise HTTPException(403, "Invalid request origin.")
    if origin_parts.netloc.lower() != request.headers.get("host", "").lower() or origin_parts.scheme not in {"http", "https"}:
        raise HTTPException(403, "Cross-origin dashboard requests are blocked.")


def require_admin(request: Request) -> None:
    verify_origin(request)
    session = request.cookies.get(SESSION_COOKIE, "")
    expiry = SESSIONS.get(session, 0)
    now = time.time()
    if not session or expiry <= now:
        SESSIONS.pop(session, None)
        raise HTTPException(401, "Please sign in to the local dashboard.")
    SESSIONS[session] = now + SESSION_TTL_SECONDS


def verify_local_key(request: Request) -> None:
    auth = request.headers.get("authorization", "")
    supplied = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not supplied or not secrets.compare_digest(supplied, LOCAL_API_KEY):
        raise HTTPException(401, detail={"error": {"type": "unauthorized", "message": "Missing or invalid MosaicRoute API key."}})


def api_error(status: int, kind: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"type": kind, "message": message}})


def safe_response_header_value(value: Any, max_length: int = 200) -> str:
    """Prevent user-configured provider/model labels from breaking response headers."""
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()
    return cleaned.encode("latin-1", errors="replace").decode("latin-1")[:max_length]


def valid_base_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    if len(value) > 500:
        raise HTTPException(400, "Base URL is too long.")
    try:
        parts = urlsplit(value)
        _ = parts.port
    except ValueError:
        raise HTTPException(400, "Invalid provider URL.")
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise HTTPException(400, "Use an http(s) base URL without embedded credentials.")
    if parts.scheme == "http":
        hostname = parts.hostname.lower().rstrip(".")
        loopback_names = {"localhost", "127.0.0.1", "::1"}
        local_network = hostname.endswith(".local")
        try:
            ip = ipaddress.ip_address(hostname.strip("[]"))
            local_network = ip.is_private or ip.is_loopback
            if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
                local_network = False
        except ValueError:
            pass
        if hostname not in loopback_names and not local_network:
            raise HTTPException(400, "Use HTTPS for remote providers. Plain HTTP is allowed only for loopback, private-LAN, or .local endpoints.")
    return value



def valid_models_path(raw: str) -> str:
    value = (raw or "/models").strip()
    if not value.startswith("/") or value.startswith("//") or ".." in value.split("/"):
        raise HTTPException(400, "Model discovery path must be a relative path beginning with /, such as /models.")
    if any(ch in value for ch in "?#\\") or len(value) > 180:
        raise HTTPException(400, "Model discovery path cannot contain query strings, fragments, or backslashes.")
    if not re.fullmatch(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*", value):
        raise HTTPException(400, "Model discovery path contains unsupported characters.")
    return value

def join_upstream(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def model_discovery_url(provider: dict[str, Any]) -> str:
    path = valid_models_path(str(provider.get("models_path", "/models") or "/models"))
    if provider.get("models_path_mode", "base") == "host":
        base = urlsplit(provider["base_url"])
        return urlunsplit((base.scheme, base.netloc, path, "", ""))
    return join_upstream(provider["base_url"], path)


def decode_encrypted_json(value: str, default: Any) -> Any:
    if not value:
        return default
    try:
        parsed = json.loads(decrypt_secret(value))
        return parsed
    except (RuntimeError, ValueError, TypeError, json.JSONDecodeError):
        return default


def public_provider(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    oauth_config = decode_encrypted_json(row["oauth_config_enc"], {}) if row["oauth_config_enc"] else {}
    token_bundle = decode_encrypted_json(row["api_key_enc"], {}) if row["auth_type"] == "oauth2" else {}
    return {
        "id": row["id"], "name": row["name"], "base_url": row["base_url"],
        "models_path": row["models_path"] if "models_path" in row.keys() else "/models",
        "models_path_mode": row["models_path_mode"] if "models_path_mode" in row.keys() else "base",
        "protocol": row["protocol"], "auth_type": row["auth_type"],
        "auth_header": row["auth_header"], "auth_prefix": row["auth_prefix"],
        "api_key_set": bool(row["api_key_enc"]),
        "custom_headers_set": bool(row["custom_headers_enc"]),
        "oauth_connected": bool(token_bundle.get("access_token")),
        "oauth_config": {key: oauth_config.get(key, "") for key in ("authorization_url", "token_url", "client_id", "scopes", "token_auth_method")},
        "is_active": bool(row["is_active"]), "created_at": row["created_at"],
        "cooldown_remaining": max(0, int(float(row["cooldown_until"] or 0) - time.time())),
        "rate_limits": {key: int(row[key] or 0) for key in ("rpm_limit", "rpd_limit", "tpm_limit", "tpd_limit", "max_concurrency") if key in row.keys()},
    }


def provider_rows() -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute("SELECT * FROM providers ORDER BY name COLLATE NOCASE").fetchall()


ROUTE_COUNTERS: dict[str, int] = {}


def _sticky_ttl_seconds() -> int:
    try:
        return max(0, min(86400, int(os.getenv("MOSAIC_STICKY_SESSION_TTL_SECONDS", "1800"))))
    except (TypeError, ValueError):
        return 1800


def get_sticky_session_key(request: Request, body: dict[str, Any], alias: str, protocol: str) -> str:
    """Return a keyed hash of caller-supplied session identity, never the raw ID."""
    raw = request.headers.get("x-mosaicroute-session") or request.headers.get("x-session-id")
    if not raw:
        user_value = body.get("user")
        metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
        raw = metadata.get("session_id") or metadata.get("conversation_id") or (user_value if isinstance(user_value, str) else "")
    raw = str(raw or "").strip()
    if not raw or len(raw) > 512 or _sticky_ttl_seconds() == 0:
        return ""
    # Pepper with the local API key so raw or guessable session identifiers are not
    # stored directly in SQLite and cannot be matched across separate installs.
    material = f"{LOCAL_API_KEY}\0{protocol}\0{alias}\0{raw}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def order_by_sticky_session(candidates: list[dict[str, Any]], alias: str, protocol: str, session_hash: str) -> list[dict[str, Any]]:
    if not candidates or not session_hash:
        return candidates
    now = time.time()
    with db() as conn:
        conn.execute("DELETE FROM sticky_sessions WHERE expires_at <= ?", (now,))
        row = conn.execute("SELECT provider_id,model_id FROM sticky_sessions WHERE session_hash=? AND alias=? AND protocol=? AND expires_at> ?",
                           (session_hash, alias, protocol, now)).fetchone()
    if not row:
        return candidates
    for idx, candidate in enumerate(candidates):
        if int(candidate["id"]) == int(row["provider_id"]) and str(candidate["upstream_model"]) == str(row["model_id"]):
            return [candidate, *candidates[:idx], *candidates[idx + 1:]]
    return candidates


def remember_sticky_session(session_hash: str, alias: str, protocol: str, candidate: dict[str, Any]) -> None:
    ttl = _sticky_ttl_seconds()
    if not session_hash or ttl <= 0:
        return
    now = time.time()
    stamp = utc_now()
    with db() as conn:
        conn.execute("""INSERT INTO sticky_sessions(session_hash,alias,protocol,provider_id,model_id,created_at,updated_at,expires_at)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(session_hash,alias,protocol) DO UPDATE SET
            provider_id=excluded.provider_id,model_id=excluded.model_id,updated_at=excluded.updated_at,expires_at=excluded.expires_at""",
            (session_hash, alias, protocol, int(candidate["id"]), str(candidate["upstream_model"]), stamp, stamp, now + ttl))


def _model_metadata(provider_id: int, model_id: str) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT metadata_json FROM discovered_models WHERE provider_id=? AND model_id=?", (provider_id, model_id)).fetchone()
    if not row:
        return {}
    try:
        value = json.loads(row["metadata_json"] or "{}")
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def resolve_candidates(alias: str, protocol: str) -> list[dict[str, Any]]:
    with db() as conn:
        route = conn.execute("SELECT candidates_json,strategy FROM model_routes WHERE alias = ?", (alias,)).fetchone()
        if route:
            ids = json.loads(route["candidates_json"])
            strategy = route["strategy"] or "balanced"
        else:
            # Any discovered upstream model ID is directly callable. Providers
            # advertising the same ID form a pool only for the same wire protocol.
            found = conn.execute(
                "SELECT dm.provider_id,dm.model_id AS upstream_model FROM discovered_models dm "
                "JOIN providers p ON p.id=dm.provider_id "
                "WHERE dm.model_id=? AND dm.is_available=1 AND p.is_active=1 AND p.protocol=? "
                "ORDER BY dm.last_seen_at DESC LIMIT 12", (alias, protocol)
            ).fetchall()
            if not found:
                return []
            ids = [dict(row) for row in found]
            strategy = "balanced"
        resolved: list[dict[str, Any]] = []
        for candidate in ids:
            row = conn.execute("SELECT * FROM providers WHERE id = ? AND is_active = 1", (candidate["provider_id"],)).fetchone()
            if not row or row["protocol"] != protocol:
                continue
            item = dict(row)
            item["upstream_model"] = candidate["upstream_model"]
            item["api_key"] = decrypt_secret(item["api_key_enc"])
            item["custom_headers"] = decode_encrypted_json(item["custom_headers_enc"], {})
            item["oauth_config"] = decode_encrypted_json(item["oauth_config_enc"], {})
            item["model_metadata"] = _model_metadata(int(item["id"]), str(item["upstream_model"]))
            resolved.append(item)
        healthy = [item for item in resolved if float(item.get("cooldown_until", 0)) <= time.time()]
        pool = healthy or resolved
        if not pool:
            return []
        if strategy == "priority" or len(pool) < 2:
            return pool

        rankings: dict[tuple[int, str], tuple[float, float, int]] = {}
        with db() as conn:
            for item in pool:
                metric = conn.execute("SELECT successes,failures,avg_latency_ms FROM provider_model_metrics WHERE provider_id=? AND model_id=?", (item["id"], item["upstream_model"])).fetchone()
                if metric is None:
                    metric = conn.execute("SELECT successes,failures,avg_latency_ms FROM provider_metrics WHERE provider_id=?", (item["id"],)).fetchone()
                key = (int(item["id"]), str(item["upstream_model"]))
                if metric:
                    total = int(metric["successes"]) + int(metric["failures"])
                    rankings[key] = ((int(metric["successes"]) / total) if total else 0.5,
                                     float(metric["avg_latency_ms"] or 999999), total)
                else:
                    rankings[key] = (0.5, 999999.0, 0)

        def key_for(item: dict[str, Any]) -> tuple[int, str]:
            return (int(item["id"]), str(item["upstream_model"]))

        def unexplored(item: dict[str, Any]) -> int:
            return 0 if rankings[key_for(item)][2] < 3 else 1

        if strategy == "round_robin":
            counter = ROUTE_COUNTERS.get(alias, 0)
            ROUTE_COUNTERS[alias] = counter + 1
            offset = counter % len(pool)
            return pool[offset:] + pool[:offset]
        if strategy == "cheapest":
            def cost_key(item: dict[str, Any]):
                metadata = item.get("model_metadata") or {}
                input_price = _as_float(metadata.get("input_price"))
                output_price = _as_float(metadata.get("output_price"))
                known = input_price is not None and output_price is not None
                price = (input_price or 0) + (output_price or 0)
                success, latency, _ = rankings[key_for(item)]
                return (0 if known else 1, price if known else float("inf"), -success, latency, unexplored(item))
            return sorted(pool, key=cost_key)
        if not any(rankings[key_for(item)][2] for item in pool):
            return pool
        if strategy == "fastest":
            return sorted(pool, key=lambda item: (unexplored(item), rankings[key_for(item)][1], -rankings[key_for(item)][0]))
        if strategy == "reliable":
            return sorted(pool, key=lambda item: (unexplored(item), -rankings[key_for(item)][0], rankings[key_for(item)][1]))
        return sorted(pool, key=lambda item: (unexplored(item), -(0.75 * rankings[key_for(item)][0] + 0.25 * (1 / (1 + rankings[key_for(item)][1] / 1000))), rankings[key_for(item)][1]))


def parse_retry_after(value: str | None) -> int | None:
    if not value:
        return None
    raw = value.strip()
    try:
        seconds = int(raw)
        return max(0, min(86400, seconds))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, min(86400, int((dt - datetime.now(timezone.utc)).total_seconds())))
    except (TypeError, ValueError, OverflowError):
        return None


def note_provider_result(provider_id: int, ok: bool, status: int = 0, latency_ms: int | None = None,
                         model_id: str | None = None, retry_after: int | None = None) -> None:
    latency = max(0, int(latency_ms or 0))
    with db() as conn:
        if ok:
            conn.execute("UPDATE providers SET failure_count=0,cooldown_until=0 WHERE id=?", (provider_id,))
        elif status in TRANSIENT_STATUSES or status in {0, 401, 402, 403}:
            row = conn.execute("SELECT failure_count FROM providers WHERE id=?", (provider_id,)).fetchone()
            failures = min(8, (row["failure_count"] if row else 0) + 1)
            cooldown = min(300, 10 * (2 ** min(failures - 1, 5)))
            if status == 429 and retry_after is not None:
                # Honor upstream backoff, including providers that ask for longer waits.
                cooldown = min(86400, max(cooldown, int(retry_after)))
            conn.execute("UPDATE providers SET failure_count=?,cooldown_until=? WHERE id=?", (failures, time.time() + cooldown, provider_id))

        metric = conn.execute("SELECT * FROM provider_metrics WHERE provider_id=?", (provider_id,)).fetchone()
        if metric:
            old_successes = int(metric["successes"])
            successes = old_successes + (1 if ok else 0)
            failures = int(metric["failures"]) + (0 if ok else 1)
            prior_success_latency = float(metric["avg_latency_ms"] or 0)
            average_latency = ((prior_success_latency * old_successes + latency) / successes) if ok and successes else (prior_success_latency or 999999.0)
            conn.execute("UPDATE provider_metrics SET successes=?,failures=?,avg_latency_ms=?,last_status=?,updated_at=? WHERE provider_id=?",
                         (successes, failures, average_latency, status, utc_now(), provider_id))
        else:
            conn.execute("INSERT INTO provider_metrics(provider_id,successes,failures,avg_latency_ms,last_status,updated_at) VALUES(?,?,?,?,?,?)",
                         (provider_id, 1 if ok else 0, 0 if ok else 1, float(latency) if ok else 999999.0, status, utc_now()))

        if model_id:
            metric = conn.execute("SELECT * FROM provider_model_metrics WHERE provider_id=? AND model_id=?", (provider_id, model_id)).fetchone()
            if metric:
                old_successes = int(metric["successes"])
                successes = old_successes + (1 if ok else 0)
                failures = int(metric["failures"]) + (0 if ok else 1)
                prior_latency = float(metric["avg_latency_ms"] or 0)
                average_latency = ((prior_latency * old_successes + latency) / successes) if ok and successes else (prior_latency or 999999.0)
                conn.execute("UPDATE provider_model_metrics SET successes=?,failures=?,avg_latency_ms=?,last_status=?,updated_at=? WHERE provider_id=? AND model_id=?",
                             (successes, failures, average_latency, status, utc_now(), provider_id, model_id))
            else:
                conn.execute("INSERT INTO provider_model_metrics(provider_id,model_id,successes,failures,avg_latency_ms,last_status,updated_at) VALUES(?,?,?,?,?,?,?)",
                             (provider_id, model_id, 1 if ok else 0, 0 if ok else 1, float(latency) if ok else 999999.0, status, utc_now()))


def log_request(alias: str, provider: str, model: str, started: float, status: int,
                usage: dict[str, Any] | None = None, error: str = "") -> None:
    usage = usage or {}
    try:
        prompt_tokens = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
        completion_tokens = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
    except (TypeError, ValueError):
        prompt_tokens = completion_tokens = 0
    with db() as conn:
        conn.execute(
            "INSERT INTO request_logs(created_at,model_alias,upstream_provider,upstream_model,latency_ms,status_code,prompt_tokens,completion_tokens,error_text) VALUES(?,?,?,?,?,?,?,?,?)",
            (utc_now(), alias, provider, model, int((time.perf_counter() - started) * 1000), status,
             prompt_tokens, completion_tokens, error[:500]),
        )


def extract_usage(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    usage = payload.get("usage")
    return usage if isinstance(usage, dict) else {}


def usage_token_count(usage: dict[str, Any] | None) -> int | None:
    if not isinstance(usage, dict):
        return None
    try:
        value = int(usage.get("total_tokens", 0) or 0)
        if value <= 0:
            value = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0) + int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def estimate_request_tokens(body: dict[str, Any]) -> int:
    # A deliberately conservative approximate reservation, not a tokenizer.
    size = len(json.dumps(body, ensure_ascii=False, separators=(",", ":")))
    prompt_estimate = max(1, (size + 3) // 4)
    output_limit = body.get("max_completion_tokens", body.get("max_tokens", 512))
    try:
        output_estimate = max(0, min(int(output_limit or 512), 32768))
    except (TypeError, ValueError):
        output_estimate = 512
    return min(10_000_000, prompt_estimate + output_estimate)


def _effective_quota_limits(provider_id: int, model_id: str) -> tuple[dict[str, int], dict[str, int]]:
    """Return provider-wide limits and model-specific limits (0 means unlimited)."""
    with db() as conn:
        provider = conn.execute("SELECT rpm_limit,rpd_limit,tpm_limit,tpd_limit,max_concurrency FROM providers WHERE id=?", (provider_id,)).fetchone()
    provider_limits = {key: int(provider[key] or 0) for key in ("rpm_limit", "rpd_limit", "tpm_limit", "tpd_limit", "max_concurrency")} if provider else {}
    metadata = _model_metadata(provider_id, model_id)
    model_limits: dict[str, int] = {}
    for key in ("rpm_limit", "rpd_limit", "tpm_limit", "tpd_limit", "max_concurrency"):
        raw = metadata.get(key)
        if raw is None and key == "max_concurrency":
            raw = metadata.get("max_concurrent_requests")
        try:
            value = int(raw or 0)
            model_limits[key] = value if 0 < value <= 10_000_000 else 0
        except (TypeError, ValueError):
            model_limits[key] = 0
    return provider_limits, model_limits


def reserve_provider_quota(provider_id: int, model_id: str, estimated_tokens: int) -> dict[str, Any]:
    """Atomically enforce the configured local provider/model quota ledger."""
    provider_limits, model_limits = _effective_quota_limits(provider_id, model_id)
    if not any(provider_limits.values()) and not any(model_limits.values()):
        return {"allowed": True, "event_id": None, "retry_after": 0, "reason": "quota tracking disabled"}

    now = time.time()
    utc_now_dt = datetime.now(timezone.utc)
    day_start = utc_now_dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    retry_after = 0
    blocked_reason = ""
    event_id: int | None = None
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM quota_events WHERE created_at < ? AND lease_until <= ?", (now - 3 * 86400, now))
        p = conn.execute("SELECT COUNT(*) n,COALESCE(SUM(CASE WHEN actual_tokens IS NULL THEN reserved_tokens ELSE actual_tokens END),0) tokens,MIN(created_at) oldest FROM quota_events WHERE provider_id=? AND created_at>=?", (provider_id, now - 60)).fetchone()
        p_day = conn.execute("SELECT COUNT(*) n,COALESCE(SUM(CASE WHEN actual_tokens IS NULL THEN reserved_tokens ELSE actual_tokens END),0) tokens,MIN(created_at) oldest FROM quota_events WHERE provider_id=? AND created_at>=?", (provider_id, day_start)).fetchone()
        m = conn.execute("SELECT COUNT(*) n,COALESCE(SUM(CASE WHEN actual_tokens IS NULL THEN reserved_tokens ELSE actual_tokens END),0) tokens,MIN(created_at) oldest FROM quota_events WHERE provider_id=? AND model_id=? AND created_at>=?", (provider_id, model_id, now - 60)).fetchone()
        m_day = conn.execute("SELECT COUNT(*) n,COALESCE(SUM(CASE WHEN actual_tokens IS NULL THEN reserved_tokens ELSE actual_tokens END),0) tokens,MIN(created_at) oldest FROM quota_events WHERE provider_id=? AND model_id=? AND created_at>=?", (provider_id, model_id, day_start)).fetchone()
        inflight_p = conn.execute("SELECT COUNT(*) n FROM quota_events WHERE provider_id=? AND finished_at IS NULL AND lease_until>?", (provider_id, now)).fetchone()["n"]
        inflight_m = conn.execute("SELECT COUNT(*) n FROM quota_events WHERE provider_id=? AND model_id=? AND finished_at IS NULL AND lease_until>?", (provider_id, model_id, now)).fetchone()["n"]

        checks = [
            (provider_limits.get("rpm_limit", 0), int(p["n"]), "provider requests/minute", float(p["oldest"] or now) + 60 - now),
            (provider_limits.get("rpd_limit", 0), int(p_day["n"]), "provider requests/day", day_start + 86400 - now),
            (provider_limits.get("tpm_limit", 0), int(p["tokens"]), "provider tokens/minute", float(p["oldest"] or now) + 60 - now),
            (provider_limits.get("tpd_limit", 0), int(p_day["tokens"]), "provider tokens/day", day_start + 86400 - now),
            (model_limits.get("rpm_limit", 0), int(m["n"]), "model requests/minute", float(m["oldest"] or now) + 60 - now),
            (model_limits.get("rpd_limit", 0), int(m_day["n"]), "model requests/day", day_start + 86400 - now),
            (model_limits.get("tpm_limit", 0), int(m["tokens"]), "model tokens/minute", float(m["oldest"] or now) + 60 - now),
            (model_limits.get("tpd_limit", 0), int(m_day["tokens"]), "model tokens/day", day_start + 86400 - now),
        ]
        for limit, current, label, reset_in in checks:
            unit = "tokens" if "tokens" in label else "requests"
            demand = estimated_tokens if unit == "tokens" else 1
            if limit and current + demand > limit:
                blocked_reason = f"{label} quota would be exceeded ({current}/{limit} {unit} already reserved)."
                if demand > limit:
                    retry_after = 0
                else:
                    retry_after = max(retry_after, max(1, int(reset_in)))
        for limit, current, label in (
            (provider_limits.get("max_concurrency", 0), int(inflight_p), "provider concurrent requests"),
            (model_limits.get("max_concurrency", 0), int(inflight_m), "model concurrent requests"),
        ):
            if limit and current >= limit:
                blocked_reason = f"{label} cap reached ({current}/{limit})."
                retry_after = max(retry_after, 5)
        if blocked_reason:
            conn.rollback()
            return {"allowed": False, "event_id": None, "retry_after": retry_after, "reason": blocked_reason}
        cur = conn.execute("INSERT INTO quota_events(provider_id,model_id,created_at,reserved_tokens,lease_until) VALUES(?,?,?,?,?)", (provider_id, model_id, now, max(1, int(estimated_tokens)), now + 300))
        event_id = int(cur.lastrowid)
    return {"allowed": True, "event_id": event_id, "retry_after": 0, "reason": "reserved"}


def finish_quota_event(event_id: int | None, status_code: int, actual_tokens: int | None = None) -> None:
    if not event_id:
        return
    with db() as conn:
        conn.execute("UPDATE quota_events SET status_code=?, finished_at=?, lease_until=0, actual_tokens=COALESCE(?,actual_tokens) WHERE id=?",
                     (int(status_code), time.time(), actual_tokens, event_id))


def quota_summary(provider_id: int) -> dict[str, Any]:
    now = time.time()
    minute_start = now - 60
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    with db() as conn:
        provider = conn.execute("SELECT rpm_limit,rpd_limit,tpm_limit,tpd_limit,max_concurrency FROM providers WHERE id=?", (provider_id,)).fetchone()
        if not provider:
            raise HTTPException(404, "Provider not found.")
        minute = conn.execute("SELECT COUNT(*) requests,COALESCE(SUM(COALESCE(actual_tokens,reserved_tokens)),0) tokens FROM quota_events WHERE provider_id=? AND created_at>=?", (provider_id, minute_start)).fetchone()
        day = conn.execute("SELECT COUNT(*) requests,COALESCE(SUM(COALESCE(actual_tokens,reserved_tokens)),0) tokens FROM quota_events WHERE provider_id=? AND created_at>=?", (provider_id, day_start)).fetchone()
        active = conn.execute("SELECT COUNT(*) n FROM quota_events WHERE provider_id=? AND finished_at IS NULL AND lease_until>?", (provider_id, now)).fetchone()["n"]
    limits = {key: int(provider[key] or 0) for key in ("rpm_limit", "rpd_limit", "tpm_limit", "tpd_limit", "max_concurrency")}
    return {"provider_id": provider_id, "limits": limits, "last_minute": dict(minute), "today_utc": dict(day), "in_flight": int(active)}


def _embedding_dimension(candidate: dict[str, Any]) -> int | None:
    metadata = candidate.get("model_metadata") or {}
    for key in ("dimensions", "embedding_dimensions", "output_dimension"):
        try:
            value = int(metadata.get(key))
            if 0 < value <= 1_000_000:
                return value
        except (TypeError, ValueError):
            continue
    return None


def safe_embedding_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Never silently mix embedding vector spaces during fallback."""
    if len(candidates) < 2:
        return candidates
    primary = candidates[0]
    base_model = str(primary.get("upstream_model", ""))
    base_dimension = _embedding_dimension(primary)
    if not base_model or base_dimension is None:
        return [primary]
    return [c for c in candidates if c.get("upstream_model") == base_model and _embedding_dimension(c) == base_dimension]


async def stream_raw(resp: httpx.Response, client: httpx.AsyncClient, *, alias: str,
                     provider: dict[str, Any], model: str, started: float, quota_event_id: int | None = None) -> AsyncIterator[bytes]:
    """Relay SSE/stream bytes and capture OpenAI-style usage when the upstream includes it."""
    buffer = ""
    usage: dict[str, Any] = {}
    status = resp.status_code
    error = ""
    try:
        async for chunk in resp.aiter_bytes():
            buffer += chunk.decode("utf-8", errors="ignore")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if line.startswith("data:"):
                    raw = line[5:].strip()
                    if raw and raw != "[DONE]":
                        try:
                            item = json.loads(raw)
                            if isinstance(item, dict) and isinstance(item.get("usage"), dict):
                                usage = item["usage"]
                        except json.JSONDecodeError:
                            pass
            yield chunk
    except Exception as exc:
        status = 502
        error = f"Stream interrupted: {type(exc).__name__}"
        raise
    finally:
        log_request(alias, provider["name"], model, started, status, usage=usage, error=error)
        finish_quota_event(quota_event_id, status, usage_token_count(usage))
        await resp.aclose()
        await client.aclose()


class ProviderInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(min_length=8, max_length=500)
    models_path: str = Field(default="/models", min_length=1, max_length=180)
    models_path_mode: str = Field(default="base", pattern="^(base|host)$")
    protocol: str = Field(default="openai", pattern="^(openai|anthropic)$")
    auth_type: str = Field(default="auto", pattern="^(auto|none|bearer|api_key|session|cookie|custom_header|custom_headers|oauth2)$")
    auth_header: str = Field(default="", max_length=128)
    auth_prefix: str = Field(default="", max_length=80)
    api_key: str = Field(default="", max_length=12000)
    custom_headers: dict[str, str] | None = None
    oauth_config: dict[str, str] = Field(default_factory=dict)
    rpm_limit: int = Field(default=0, ge=0, le=100000)
    rpd_limit: int = Field(default=0, ge=0, le=10000000)
    tpm_limit: int = Field(default=0, ge=0, le=1000000000)
    tpd_limit: int = Field(default=0, ge=0, le=10000000000)
    max_concurrency: int = Field(default=0, ge=0, le=10000)
    is_active: bool = True


class ModelCandidate(BaseModel):
    provider_id: int
    upstream_model: str = Field(min_length=1, max_length=300)


class ModelInput(BaseModel):
    alias: str = Field(min_length=1, max_length=120)
    candidates: list[ModelCandidate] = Field(min_length=1, max_length=12)
    strategy: str = Field(default="balanced", pattern="^(balanced|priority|fastest|reliable|cheapest|round_robin)$")


def _configured_discovery_interval() -> int:
    try:
        return max(0, min(86400, int(os.getenv("MOSAIC_DISCOVERY_INTERVAL_SECONDS", "1800"))))
    except (TypeError, ValueError):
        return 1800


DISCOVERY_REFRESH_INTERVAL_SECONDS = _configured_discovery_interval()


async def _scheduled_discovery_loop() -> None:
    # First run after startup, then at the configured interval. The worker only
    # contacts providers explicitly configured and enabled by the user.
    while True:
        try:
            active = [row for row in provider_rows() if row["is_active"]]
            if active:
                semaphore = asyncio.Semaphore(4)
                async def limited(row: sqlite3.Row) -> dict[str, Any]:
                    async with semaphore:
                        return await discover_one_provider(int(row["id"]))
                results = await asyncio.gather(*(limited(row) for row in active))
                if any(item.get("ok") for item in results):
                    try:
                        await create_auto_routes()
                    except HTTPException:
                        # No eligible discoveries yet; the next refresh can try again.
                        pass
        except asyncio.CancelledError:
            raise
        except Exception:
            # Do not crash the API service because a background catalog refresh failed.
            pass
        await asyncio.sleep(DISCOVERY_REFRESH_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = None
    if DISCOVERY_REFRESH_INTERVAL_SECONDS > 0:
        task = asyncio.create_task(_scheduled_discovery_loop())
    try:
        yield
    finally:
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(title=APP_NAME, version=APP_VERSION, docs_url=None, redoc_url=None, lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    length = request.headers.get("content-length")
    if request.url.path.startswith("/v1/") and length:
        try:
            if int(length) > MAX_BODY_BYTES:
                return api_error(413, "request_too_large", "Request body exceeds the 16 MiB local limit.")
        except ValueError:
            return api_error(400, "validation_error", "Invalid Content-Length header.")
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data: blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
    # Never let the browser cache credential-bearing dashboard/bootstrap APIs.
    if request.url.path == "/" or request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return response


@app.get("/")
async def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "app": APP_NAME, "version": APP_VERSION, "time": utc_now()}


@app.get("/api/auth/status")
async def auth_status(request: Request) -> dict[str, Any]:
    session = request.cookies.get(SESSION_COOKIE, "")
    logged_in = bool(session and SESSIONS.get(session, 0) > time.time())
    return {"logged_in": logged_in}


@app.post("/api/auth/login")
async def auth_login(request: Request):
    verify_origin(request)
    try:
        body = await request.json()
    except Exception:
        return api_error(400, "validation_error", "Expected a JSON body.")
    password = str(body.get("password", "")) if isinstance(body, dict) else ""
    if not secrets.compare_digest(password, ADMIN_PASSWORD):
        # Avoid turning the endpoint into a rapid password-guessing target.
        await __import__("asyncio").sleep(0.45)
        return api_error(401, "unauthorized", "Incorrect dashboard password.")
    token = secrets.token_urlsafe(32)
    SESSIONS[token] = time.time() + SESSION_TTL_SECONDS
    response = JSONResponse({"ok": True, "expires_in": SESSION_TTL_SECONDS})
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=False, samesite="strict", max_age=SESSION_TTL_SECONDS, path="/")
    return response


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    verify_origin(request)
    token = request.cookies.get(SESSION_COOKIE, "")
    SESSIONS.pop(token, None)
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


admin = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])


@admin.get("/bootstrap")
async def bootstrap() -> dict[str, Any]:
    return {"app": APP_NAME, "version": APP_VERSION, "local_api_key": LOCAL_API_KEY,
            "base_url": "http://127.0.0.1:" + os.getenv("MOSAIC_PORT", "8000") + "/v1",
            "model_discovery": True, "provider_count": len(PROVIDER_CATALOG),
            "security": "Local-only. Keep this app bound to 127.0.0.1 and never expose the port to the internet."}


def load_provider_catalog_file() -> list[dict[str, Any]]:
    """Load presets from JSON so catalog maintenance does not require code edits.

    The JSON file is a local trusted config shipped with the application. It is
    deliberately not downloaded and executed from an untrusted remote source.
    """
    catalog_path = BASE_DIR / "config" / "provider_catalog.json"
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not load provider catalog at {catalog_path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("providers"), list):
        raise RuntimeError("config/provider_catalog.json must contain a providers array.")
    presets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(payload["providers"]):
        if not isinstance(item, dict):
            raise RuntimeError(f"Provider preset #{index + 1} is not an object.")
        provider_id = str(item.get("id", "")).strip()
        name = str(item.get("name", "")).strip()
        base_url = str(item.get("base_url", "")).strip()
        protocol = str(item.get("protocol", "openai")).strip()
        if not provider_id or not name or not base_url:
            raise RuntimeError(f"Provider preset #{index + 1} needs id, name, and base_url.")
        if provider_id in seen:
            raise RuntimeError(f"Duplicate provider preset id in catalog: {provider_id}.")
        if protocol not in {"openai", "anthropic"}:
            raise RuntimeError(f"Unsupported protocol in provider preset {provider_id}: {protocol}.")
        seen.add(provider_id)
        # Keep only plain JSON data; do not eval expressions or support code hooks.
        presets.append(dict(item, id=provider_id, name=name, base_url=base_url, protocol=protocol))
    if not presets:
        raise RuntimeError("Provider catalog is empty; refusing to start with no presets.")
    return presets


PROVIDER_CATALOG = load_provider_catalog_file()
FREE_TIER_NOTES: dict[str, tuple[str, str]] = {
    item["id"]: (
        str(item.get("free_tier_status", "unknown")),
        str(item.get("access_note", "Free access is not guaranteed; check your provider account and current terms.")),
    )
    for item in PROVIDER_CATALOG
}


@admin.get("/catalog/providers")
async def provider_catalog() -> list[dict[str, Any]]:
    enriched=[]
    for item in PROVIDER_CATALOG:
        row=dict(item)
        status, note = FREE_TIER_NOTES.get(row["id"], ("unknown", "Free access is not guaranteed; check your provider account and current terms."))
        row["free_tier_status"] = status
        row["access_note"] = row.get("access_note") or note
        row["model_discovery"] = "configurable"
        enriched.append(row)
    return enriched


@admin.get("/providers")
async def list_providers() -> list[dict[str, Any]]:
    return [public_provider(row) for row in provider_rows()]


def _as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        result = float(value)
        return result if result >= 0 else None
    except (TypeError, ValueError):
        return None


def normalize_discovered_models(payload: Any) -> list[dict[str, Any]]:
    """Normalize common model-list shapes without assuming a price is zero."""
    rows: Any = []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        for key in ("data", "models", "results", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                rows = value
                break
            if isinstance(value, dict):
                for nested in ("data", "models", "results", "items"):
                    if isinstance(value.get(nested), list):
                        rows = value[nested]
                        break
                if isinstance(rows, list) and rows:
                    break
    normalized: dict[str, dict[str, Any]] = {}
    for raw in rows if isinstance(rows, list) else []:
        if isinstance(raw, str):
            model_id, meta = raw.strip(), {}
        elif isinstance(raw, dict):
            model_id = str(raw.get("id") or raw.get("model") or raw.get("model_id") or raw.get("name") or "").strip()
            meta = raw
        else:
            continue
        if model_id.startswith("models/"):
            # Google-native list shapes often prefix names with `models/`.
            model_id = model_id.split("/", 1)[1]
        if not model_id or len(model_id) > 300 or any(ord(ch) < 32 for ch in model_id):
            continue
        # Keep the first occurrence when providers return duplicate model IDs;
        # this preserves the richest metadata from their canonical entry.
        if model_id in normalized:
            continue
        pricing = meta.get("pricing") if isinstance(meta.get("pricing"), dict) else {}
        input_price = _as_float(meta.get("input_cost", meta.get("prompt_cost", pricing.get("prompt", pricing.get("input")))))
        output_price = _as_float(meta.get("output_cost", meta.get("completion_cost", pricing.get("completion", pricing.get("output")))))
        if input_price == 0 and output_price == 0:
            free_status = "provider-priced-zero"
        elif re.search(r"(^|[/:-])free($|[/:-])", model_id.lower()):
            # Common explicit free-route conventions: model:free, model-free,
            # provider/free and free/model. Treat these as hints, not proof.
            free_status = "explicit-free-model-id-hint"
        else:
            free_status = "unknown"
        metadata = {}
        for key in ("owned_by", "context_length", "context_window", "created", "type", "architecture", "supported_parameters", "capabilities", "input_modalities", "output_modalities", "dimensions", "embedding_dimensions", "output_dimension", "rpm_limit", "rpd_limit", "tpm_limit", "tpd_limit", "max_concurrency", "max_concurrent_requests", "max_input_tokens", "max_output_tokens"):
            if key in meta and isinstance(meta[key], (str, int, float, bool, list, dict)):
                metadata[key] = meta[key]
        # Some gateways publish capabilities/limits inside a nested per-model `limits` object.
        nested_limits = meta.get("limits") if isinstance(meta.get("limits"), dict) else {}
        for key in ("rpm_limit", "rpd_limit", "tpm_limit", "tpd_limit", "max_concurrency", "max_concurrent_requests"):
            if key not in metadata and key in nested_limits:
                metadata[key] = nested_limits[key]
        if input_price is not None: metadata["input_price"] = input_price
        if output_price is not None: metadata["output_price"] = output_price
        normalized[model_id] = {
            "model_id": model_id,
            "display_name": str(meta.get("name") or model_id)[:300],
            "metadata": metadata,
            "free_status": free_status,
        }
    return list(normalized.values())


def is_local_endpoint(base_url: str) -> bool:
    try:
        host = (urlsplit(base_url).hostname or "").lower().rstrip(".")
        if host in {"localhost", "host.docker.internal"} or host.endswith(".local"):
            return True
        if host:
            ip = ipaddress.ip_address(host.strip("[]"))
            return ip.is_loopback or ip.is_private or ip.is_link_local
    except ValueError:
        return False
    return False

def _free_confidence_rank(item: dict[str, Any]) -> int:
    status = item.get("free_status", "unknown")
    return {"provider-priced-zero": 3, "explicit-free-model-id-hint": 2, "unknown": 0}.get(status, 0)


def _discovery_provider_config(row: sqlite3.Row) -> dict[str, Any]:
    provider = dict(row)
    provider["api_key"] = decrypt_secret(provider["api_key_enc"])
    provider["custom_headers"] = decode_encrypted_json(provider["custom_headers_enc"], {})
    provider["oauth_config"] = decode_encrypted_json(provider["oauth_config_enc"], {})
    return provider


def _discovery_max_pages() -> int:
    try:
        return max(1, min(100, int(os.getenv("MOSAIC_DISCOVERY_MAX_PAGES", "10"))))
    except (TypeError, ValueError):
        return 10


def _catalog_next_url(payload: Any, current_url: str, provider_base_url: str) -> tuple[str | None, bool]:
    """Resolve common catalog pagination links/tokens and reject cross-origin links.

    The second tuple item is True when a pagination link was present but unsafe.
    Credentials are never forwarded to a different origin based on upstream JSON.
    """
    if not isinstance(payload, dict):
        return None, False
    next_value: Any = None
    for key in ("next", "next_url", "next_page_url", "nextPageUrl", "nextPage"):
        if key in payload and payload[key]:
            next_value = payload[key]
            break
    links = payload.get("links") if isinstance(payload.get("links"), dict) else {}
    if not next_value and links.get("next"):
        next_value = links["next"]
    pagination = payload.get("pagination") if isinstance(payload.get("pagination"), dict) else {}
    if not next_value and pagination.get("next"):
        next_value = pagination["next"]
    if isinstance(next_value, dict):
        next_value = next_value.get("href") or next_value.get("url")

    if next_value:
        candidate_url = urljoin(current_url, str(next_value))
    else:
        nested_sources = [payload, pagination]
        token_key = token = None
        for source in nested_sources:
            for key in ("next_page_token", "nextPageToken", "next_cursor", "nextCursor"):
                if source.get(key):
                    token_key, token = key, str(source[key])
                    break
            if token:
                break
        has_more = payload.get("has_more", payload.get("hasMore", pagination.get("has_more", pagination.get("hasMore", None))))
        if token and has_more is not False:
            query_key = {"next_page_token": "page_token", "nextPageToken": "pageToken",
                         "next_cursor": "cursor", "nextCursor": "cursor"}[str(token_key)]
            parts = urlsplit(current_url)
            query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != query_key]
            query.append((query_key, token))
            candidate_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
        elif has_more:
            # OpenAI-style catalogs commonly expose has_more + last_id for after= pagination.
            cursor = payload.get("last_id") or pagination.get("last_id") or payload.get("next_cursor") or pagination.get("next_cursor")
            if not cursor:
                return None, False
            parts = urlsplit(current_url)
            query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "after"]
            query.append(("after", str(cursor)))
            candidate_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
        else:
            return None, False

    try:
        target = urlsplit(candidate_url)
        origin = urlsplit(provider_base_url)
        _ = target.port
    except ValueError:
        return None, True
    if (target.scheme != origin.scheme or target.netloc.lower() != origin.netloc.lower()
            or target.username or target.password or target.fragment or target.scheme not in {"http", "https"}):
        return None, True
    return candidate_url, False


async def fetch_model_catalog_pages(provider: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    """Fetch a bounded paginated catalog; all pages must succeed before replacing last-known-good data."""
    initial_url = model_discovery_url(provider)
    allowed_origin = provider["base_url"]
    url = initial_url
    visited: set[str] = set()
    merged: dict[str, dict[str, Any]] = {}
    started = time.perf_counter()
    pages = 0
    final_status = 200
    retry_after = None
    max_pages = _discovery_max_pages()
    try:
        async with httpx.AsyncClient(timeout=18.0, follow_redirects=False) as client:
            while url:
                if url in visited:
                    return {"ok": False, "status": 508, "count": 0, "pages": pages,
                            "latency_ms": int((time.perf_counter() - started) * 1000), "retry_after": None,
                            "message": "Model catalog pagination repeated a URL; previous catalog was preserved."}
                if pages >= max_pages:
                    return {"ok": False, "status": 413, "count": 0, "pages": pages,
                            "latency_ms": int((time.perf_counter() - started) * 1000), "retry_after": None,
                            "message": f"Model catalog exceeds the configured {max_pages}-page discovery cap. Increase MOSAIC_DISCOVERY_MAX_PAGES if this provider requires more pages."}
                visited.add(url)
                response = await client.get(url, headers=headers)
                final_status = response.status_code
                retry_after = parse_retry_after(response.headers.get("retry-after"))
                if response.status_code >= 400:
                    return {"ok": False, "status": response.status_code, "count": 0, "pages": pages + 1,
                            "latency_ms": int((time.perf_counter() - started) * 1000), "retry_after": retry_after,
                            "message": f"Model endpoint returned HTTP {response.status_code} on catalog page {pages + 1}; previous catalog was preserved."}
                try:
                    payload = response.json()
                except ValueError:
                    return {"ok": False, "status": response.status_code, "count": 0, "pages": pages + 1,
                            "latency_ms": int((time.perf_counter() - started) * 1000), "retry_after": None,
                            "message": "Model endpoint returned non-JSON content; previous catalog was preserved."}
                page_models = normalize_discovered_models(payload)
                for model in page_models:
                    # When duplicate IDs occur across pages, retain the first canonical entry.
                    merged.setdefault(model["model_id"], model)
                if len(merged) > 10000:
                    return {"ok": False, "status": 413, "count": 0, "pages": pages + 1,
                            "latency_ms": int((time.perf_counter() - started) * 1000), "retry_after": None,
                            "message": "Provider catalog exceeded 10,000 models; previous catalog was preserved."}
                next_url, unsafe = _catalog_next_url(payload, url, allowed_origin)
                if unsafe:
                    return {"ok": False, "status": 400, "count": 0, "pages": pages + 1,
                            "latency_ms": int((time.perf_counter() - started) * 1000), "retry_after": None,
                            "message": "Provider catalog returned a cross-origin or invalid pagination link. It was not followed to protect credentials; previous catalog was preserved."}
                pages += 1
                url = next_url or ""
    except httpx.HTTPError as exc:
        return {"ok": False, "status": 0, "count": 0, "pages": pages,
                "latency_ms": int((time.perf_counter() - started) * 1000), "retry_after": None,
                "message": f"Discovery request failed ({type(exc).__name__}); previous catalog was preserved."}
    models = list(merged.values())
    return {"ok": bool(models), "status": final_status, "models": models, "count": len(models), "pages": pages,
            "latency_ms": int((time.perf_counter() - started) * 1000), "retry_after": retry_after,
            "message": f"Discovered {len(models)} model(s) across {pages} catalog page(s)." if models else "Endpoint returned no recognizable model IDs; previous catalog was preserved."}


async def discover_one_provider(provider_id: int) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
    if not row:
        return {"provider_id": provider_id, "ok": False, "count": 0, "message": "Provider not found."}
    provider = _discovery_provider_config(row)
    started = time.perf_counter()
    try:
        headers = await provider_auth_headers(provider, provider["protocol"])
        catalog = await fetch_model_catalog_pages(provider, headers)
        elapsed = int(catalog.get("latency_ms", (time.perf_counter() - started) * 1000))
        if not catalog.get("ok"):
            status = int(catalog.get("status", 0))
            note_provider_result(provider_id, False, status, elapsed, retry_after=catalog.get("retry_after"))
            return {"provider_id": provider_id, "provider": provider["name"], "ok": False, "status": status,
                    "count": 0, "pages": int(catalog.get("pages", 0)), "latency_ms": elapsed,
                    "message": catalog.get("message", "Discovery failed; the previous catalog was preserved.")}
        models = catalog["models"]
        seen_at = utc_now()
        with db() as conn:
            conn.execute("UPDATE discovered_models SET is_available=0 WHERE provider_id=?", (provider_id,))
            for model in models:
                conn.execute("""INSERT INTO discovered_models(provider_id,model_id,display_name,metadata_json,free_status,is_available,discovered_at,last_seen_at)
                    VALUES(?,?,?,?,?,1,?,?) ON CONFLICT(provider_id,model_id) DO UPDATE SET
                    display_name=excluded.display_name, metadata_json=excluded.metadata_json, free_status=excluded.free_status,
                    is_available=1, discovered_at=excluded.discovered_at, last_seen_at=excluded.last_seen_at""",
                    (provider_id, model["model_id"], model["display_name"], json.dumps(model["metadata"], separators=(",", ":")), model["free_status"], seen_at, seen_at))
        note_provider_result(provider_id, True, int(catalog.get("status", 200)), elapsed)
        auto_routes = None
        try:
            auto_routes = await create_auto_routes()
        except HTTPException:
            auto_routes = None
        return {"provider_id": provider_id, "provider": provider["name"], "ok": True, "status": int(catalog.get("status", 200)),
                "count": len(models), "pages": int(catalog.get("pages", 1)), "latency_ms": elapsed, "auto_routes": auto_routes,
                "message": catalog.get("message", f"Discovered {len(models)} model(s).")}
    except (httpx.HTTPError, ValueError, RuntimeError, KeyError) as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        note_provider_result(provider_id, False, 0, elapsed)
        return {"provider_id": provider_id, "provider": provider["name"], "ok": False, "status": 0, "count": 0,
                "latency_ms": elapsed, "message": f"Discovery failed ({type(exc).__name__}). Check connectivity, auth, and endpoint path."}


@admin.post("/providers/{provider_id}/discover")
async def discover_provider(provider_id: int) -> dict[str, Any]:
    return await discover_one_provider(provider_id)


@admin.post("/discovery/refresh")
async def refresh_discovery() -> dict[str, Any]:
    rows = [row for row in provider_rows() if row["is_active"]]
    semaphore = asyncio.Semaphore(4)
    async def limited(row: sqlite3.Row) -> dict[str, Any]:
        async with semaphore:
            return await discover_one_provider(int(row["id"]))
    results = await asyncio.gather(*(limited(row) for row in rows))
    with db() as conn:
        available = conn.execute("SELECT COUNT(*) n FROM discovered_models WHERE is_available=1").fetchone()["n"]
    route_result = None
    if any(item.get("ok") for item in results):
        try:
            route_result = await create_auto_routes()
        except HTTPException:
            route_result = {"created_or_updated": [], "skipped": [], "note": "No eligible models were discovered for smart routes yet."}
    return {"providers_checked": len(results), "providers_succeeded": sum(bool(item["ok"]) for item in results),
            "models_available": int(available), "auto_routes": route_result, "results": results}


@admin.get("/discovery/models")
async def discovery_models(provider_id: int | None = None, query: str = "", limit: int = 1000) -> list[dict[str, Any]]:
    limit = min(3000, max(1, int(limit)))
    where = ["dm.is_available=1"]
    params: list[Any] = []
    if provider_id is not None:
        where.append("dm.provider_id=?"); params.append(provider_id)
    if query.strip():
        where.append("(dm.model_id LIKE ? OR dm.display_name LIKE ? OR p.name LIKE ?)")
        q = f"%{query.strip()[:120]}%"; params.extend([q, q, q])
    params.append(limit)
    with db() as conn:
        rows = conn.execute(f"""SELECT dm.*,p.name provider_name,p.protocol,p.base_url
            FROM discovered_models dm JOIN providers p ON p.id=dm.provider_id
            WHERE {' AND '.join(where)} ORDER BY p.name COLLATE NOCASE,dm.model_id COLLATE NOCASE LIMIT ?""", params).fetchall()
    result=[]
    for row in rows:
        result.append({"provider_id": row["provider_id"], "provider_name": row["provider_name"], "protocol": row["protocol"],
                       "base_url": row["base_url"], "model_id": row["model_id"], "display_name": row["display_name"],
                       "metadata": json.loads(row["metadata_json"] or "{}"), "free_status": row["free_status"],
                       "discovered_at": row["discovered_at"], "last_seen_at": row["last_seen_at"]})
    return result


@admin.post("/discovery/create-auto-routes")
async def create_auto_routes() -> dict[str, Any]:
    """Create conservative helper aliases from the currently discovered catalog."""
    with db() as conn:
        rows = conn.execute("""SELECT dm.provider_id,dm.model_id,dm.display_name,dm.metadata_json,dm.free_status,
            p.name provider_name,p.protocol,p.base_url,p.is_active
            FROM discovered_models dm JOIN providers p ON p.id=dm.provider_id
            WHERE dm.is_available=1 AND p.is_active=1 ORDER BY p.name,dm.model_id""").fetchall()
        metrics = {int(row["provider_id"]): row for row in conn.execute("SELECT provider_id,successes,failures,avg_latency_ms FROM provider_metrics").fetchall()}
    if not rows:
        with db() as conn:
            conn.execute("DELETE FROM model_routes WHERE is_auto_generated=1")
        return {"created_or_updated": [], "skipped": [{"alias": "all", "reason": "No active discovered models are available."}],
                "note": "Discover at least one provider to build smart routes. Stale generated aliases were removed; manually authored aliases were preserved."}
    catalog = []
    for row in rows:
        metadata = json.loads(row["metadata_json"] or "{}")
        metric = metrics.get(int(row["provider_id"]))
        total = (int(metric["successes"]) + int(metric["failures"])) if metric else 0
        reliability = (int(metric["successes"]) / total) if total else 0.5
        latency = float(metric["avg_latency_ms"] or 999999) if metric else 999999.0
        model_id = row["model_id"]
        search = (model_id + " " + row["provider_name"]).lower()
        local = is_local_endpoint(row["base_url"])
        priced = metadata.get("input_price") is not None and metadata.get("output_price") is not None
        price = (metadata.get("input_price", 0) or 0) + (metadata.get("output_price", 0) or 0)
        catalog.append({"provider_id": int(row["provider_id"]), "upstream_model": model_id, "protocol": row["protocol"],
                        "model_id": model_id, "provider_name": row["provider_name"], "search": search,
                        "local": local, "free_status": row["free_status"], "priced": priced, "price": price,
                        "reliability": reliability, "latency": latency, "metadata": metadata})

    def rank(item: dict[str, Any], intent: str) -> tuple[float, float, float]:
        reliability = item["reliability"]
        latency = min(item["latency"], 600000)
        speed_score = 1 / (1 + latency / 1000)
        price_score = item["price"] if item["priced"] else 1e6
        hints = 1 if any(term in item["search"] for term in ("coder", "coding", "code", "devstral", "qwen3-coder")) else 0
        if intent == "coding": return (-hints, -reliability, latency)
        if intent == "fast": return (latency, -reliability, price_score)
        if intent == "cheap": return (price_score, -reliability, latency)
        if intent == "free": return (-_free_confidence_rank(item), -reliability, latency)
        if intent == "offline": return (0 if item["local"] else 1, -reliability, latency)
        if intent == "long-context":
            context = _as_float(item["metadata"].get("context_window", item["metadata"].get("context_length"))) or 0
            return (-context, -reliability, latency)
        if intent == "vision": return (-reliability, latency, price_score)
        if intent == "embedding": return (-reliability, latency, price_score)
        return (-reliability, -speed_score, price_score)

    def context_capacity(model: dict[str, Any]) -> float:
        return _as_float(model["metadata"].get("context_window", model["metadata"].get("context_length"))) or 0

    def modality_text(model: dict[str, Any]) -> str:
        return (str(model["metadata"].get("type", "")) + " " + str(model["metadata"].get("capabilities", "")) + " " + str(model["metadata"].get("input_modalities", "")) + " " + str(model["metadata"].get("output_modalities", "")) + " " + model["search"]).lower()

    specs = [
        ("auto", "balanced", lambda m: m["protocol"] == "openai"),
        ("auto/coding", "reliable", lambda m: m["protocol"] == "openai" and any(x in m["search"] for x in ("coder", "coding", "code", "devstral"))),
        ("auto/fast", "fastest", lambda m: m["protocol"] == "openai"),
        ("auto/cheap", "cheapest", lambda m: m["protocol"] == "openai" and m["priced"]),
        ("auto/free", "balanced", lambda m: m["protocol"] == "openai" and _free_confidence_rank(m) >= 2),
        ("auto/offline", "balanced", lambda m: m["protocol"] == "openai" and m["local"]),
        ("auto/local", "balanced", lambda m: m["protocol"] == "openai" and m["local"]),
        ("auto/reliable", "reliable", lambda m: m["protocol"] == "openai"),
        ("auto/vision", "reliable", lambda m: m["protocol"] == "openai" and any(x in modality_text(m) for x in ("vision", "image", "multimodal"))),
        ("auto/long-context", "balanced", lambda m: m["protocol"] == "openai" and context_capacity(m) >= 32768),
        ("auto/embedding", "balanced", lambda m: m["protocol"] == "openai" and any(x in modality_text(m) for x in ("embedding", "embed", "rerank"))),
        ("auto-anthropic", "balanced", lambda m: m["protocol"] == "anthropic"),
    ]
    written=[]; skipped=[]
    with db() as conn:
        for alias, strategy, predicate in specs:
            existing = conn.execute("SELECT is_auto_generated FROM model_routes WHERE alias=?", (alias,)).fetchone()
            if existing and not existing["is_auto_generated"]:
                # Never let automated discovery overwrite an alias the user owns.
                skipped.append({"alias": alias, "reason": "A manually managed alias with this name already exists."})
                continue
            picks = [m for m in catalog if predicate(m)]
            picks.sort(key=lambda m: rank(m, {"auto":"balanced", "auto/coding":"coding", "auto/fast":"fast", "auto/cheap":"cheap", "auto/free":"free", "auto/offline":"offline", "auto/local":"offline", "auto/reliable":"balanced", "auto/vision":"vision", "auto/long-context":"long-context", "auto/embedding":"embedding", "auto-anthropic":"balanced"}[alias]))
            # Keep a bounded fallback set and ensure one model per provider before repeats.
            unique=[]; seen_providers=set()
            for item in picks:
                key=item["provider_id"]
                if key in seen_providers: continue
                unique.append(item); seen_providers.add(key)
                if len(unique)>=12: break
            if not unique:
                # Remove only aliases previously owned by discovery; never delete
                # a manually authored alias with the same name.
                conn.execute("DELETE FROM model_routes WHERE alias=? AND is_auto_generated=1", (alias,))
                skipped.append({"alias": alias, "reason": "No candidates match the discovery metadata and current intent."})
                continue
            candidates=[{"provider_id": item["provider_id"], "upstream_model": item["upstream_model"]} for item in unique]
            conn.execute("INSERT INTO model_routes(alias,candidates_json,strategy,is_auto_generated,created_at) VALUES(?,?,?,?,?) ON CONFLICT(alias) DO UPDATE SET candidates_json=excluded.candidates_json,strategy=excluded.strategy,is_auto_generated=1",
                         (alias,json.dumps(candidates),strategy,1,utc_now()))
            written.append({"alias":alias,"candidates":len(candidates),"strategy":strategy})
    return {"created_or_updated":written,"skipped":skipped,"note":"auto/free includes only model IDs explicitly marked as free-like or models with provider-reported zero pricing. An ID hint is not a guarantee of continued free access."}


def validate_header_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", cleaned):
        raise HTTPException(400, f"Invalid HTTP header name: {name!r}.")
    if cleaned.lower() in {"host", "content-length", "connection", "transfer-encoding", "proxy-authorization", "proxy-connection", "upgrade"}:
        raise HTTPException(400, f"The {cleaned} header is managed by the HTTP client and cannot be overridden.")
    return cleaned


def normalize_custom_headers(headers: dict[str, str] | None) -> dict[str, str] | None:
    if headers is None:
        return None
    if len(headers) > 40:
        raise HTTPException(400, "Use no more than 40 custom headers.")
    result: dict[str, str] = {}
    total = 0
    for raw_name, raw_value in headers.items():
        name = validate_header_name(str(raw_name))
        value = str(raw_value)
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise HTTPException(400, f"Header {name} contains a control character.")
        total += len(name) + len(value)
        result[name] = value
    if total > 8000:
        raise HTTPException(400, "Custom headers exceed 8 KB.")
    return result


def valid_oauth_url(raw: str, label: str) -> str:
    value = raw.strip()
    try:
        parts = urlsplit(value)
        _ = parts.port
    except ValueError:
        raise HTTPException(400, f"Invalid OAuth {label} URL.")
    if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost", "::1"}):
        raise HTTPException(400, f"OAuth {label} URL must use HTTPS (HTTP is allowed only for loopback development endpoints).")
    if not parts.hostname or parts.username or parts.password or parts.fragment:
        raise HTTPException(400, f"Invalid OAuth {label} URL.")
    return value


def prepare_provider_secrets(payload: ProviderInput, old: sqlite3.Row | None = None) -> tuple[str, str, str]:
    raw_key = payload.api_key.strip()
    if "\r" in raw_key or "\n" in raw_key:
        raise HTTPException(400, "Credentials cannot contain line breaks.")
    if raw_key:
        key_enc = encrypt_secret(raw_key)
    elif old and old["auth_type"] == payload.auth_type:
        key_enc = old["api_key_enc"]
    else:
        key_enc = ""

    custom_headers = normalize_custom_headers(payload.custom_headers)
    if custom_headers is None and old:
        custom_enc = old["custom_headers_enc"]
    else:
        custom_enc = encrypt_secret(json.dumps(custom_headers or {}, separators=(",", ":"))) if custom_headers else ""

    oauth_config = {str(k): str(v) for k, v in (payload.oauth_config or {}).items() if isinstance(v, (str, int, float))}
    if old and old["oauth_config_enc"]:
        existing = decode_encrypted_json(old["oauth_config_enc"], {})
        merged = dict(existing)
        for key, value in oauth_config.items():
            if value.strip():
                merged[key] = value.strip()
        # Do not silently discard provider app credentials when the UI intentionally leaves secrets blank.
        oauth_config = merged
    if payload.auth_type == "oauth2":
        for required_key in ("authorization_url", "token_url", "client_id"):
            if not oauth_config.get(required_key, "").strip():
                raise HTTPException(400, f"OAuth 2.0 requires {required_key.replace('_', ' ')}.")
        oauth_config["authorization_url"] = valid_oauth_url(oauth_config["authorization_url"], "authorization")
        oauth_config["token_url"] = valid_oauth_url(oauth_config["token_url"], "token")
        if oauth_config.get("token_auth_method", "client_secret_post") not in {"client_secret_post", "client_secret_basic", "none"}:
            raise HTTPException(400, "OAuth token auth method must be client_secret_post, client_secret_basic, or none.")
    oauth_enc = encrypt_secret(json.dumps(oauth_config, separators=(",", ":"))) if oauth_config else (old["oauth_config_enc"] if old else "")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in payload.auth_header + payload.auth_prefix):
        raise HTTPException(400, "Authentication header configuration cannot contain control characters.")
    if payload.auth_header.strip():
        validate_header_name(payload.auth_header)
    return key_enc, custom_enc, oauth_enc


@admin.get("/providers/{provider_id}/quota")
async def provider_quota(provider_id: int) -> dict[str, Any]:
    return quota_summary(provider_id)


@admin.post("/providers")
async def create_provider(payload: ProviderInput) -> dict[str, Any]:
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Provider name cannot be blank.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise HTTPException(400, "Provider name cannot contain control characters.")
    base_url = valid_base_url(payload.base_url)
    key_enc, custom_enc, oauth_enc = prepare_provider_secrets(payload)
    with db() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO providers(name,base_url,models_path,models_path_mode,api_key_enc,protocol,auth_type,auth_header,auth_prefix,custom_headers_enc,oauth_config_enc,is_active,rpm_limit,rpd_limit,tpm_limit,tpd_limit,max_concurrency,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (name, base_url, valid_models_path(payload.models_path), payload.models_path_mode, key_enc, payload.protocol, payload.auth_type, payload.auth_header.strip(), payload.auth_prefix, custom_enc, oauth_enc, int(payload.is_active), payload.rpm_limit, payload.rpd_limit, payload.tpm_limit, payload.tpd_limit, payload.max_concurrency, utc_now()),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "A provider with that name already exists.")
        row = conn.execute("SELECT * FROM providers WHERE id=?", (cur.lastrowid,)).fetchone()
    return public_provider(row)


@admin.put("/providers/{provider_id}")
async def update_provider(provider_id: int, payload: ProviderInput) -> dict[str, Any]:
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Provider name cannot be blank.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise HTTPException(400, "Provider name cannot contain control characters.")
    base_url = valid_base_url(payload.base_url)
    with db() as conn:
        old = conn.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
        if not old:
            raise HTTPException(404, "Provider not found.")
        key_enc, custom_enc, oauth_enc = prepare_provider_secrets(payload, old)
        try:
            conn.execute(
                "UPDATE providers SET name=?,base_url=?,models_path=?,models_path_mode=?,api_key_enc=?,protocol=?,auth_type=?,auth_header=?,auth_prefix=?,custom_headers_enc=?,oauth_config_enc=?,is_active=?,rpm_limit=?,rpd_limit=?,tpm_limit=?,tpd_limit=?,max_concurrency=? WHERE id=?",
                (name, base_url, valid_models_path(payload.models_path), payload.models_path_mode, key_enc, payload.protocol, payload.auth_type, payload.auth_header.strip(), payload.auth_prefix, custom_enc, oauth_enc, int(payload.is_active), payload.rpm_limit, payload.rpd_limit, payload.tpm_limit, payload.tpd_limit, payload.max_concurrency, provider_id),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "A provider with that name already exists.")
        row = conn.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
    return public_provider(row)


@admin.delete("/providers/{provider_id}")
async def delete_provider(provider_id: int) -> dict[str, bool]:
    with db() as conn:
        references = conn.execute("SELECT alias,candidates_json FROM model_routes").fetchall()
        for route in references:
            candidates = [c for c in json.loads(route["candidates_json"]) if c["provider_id"] != provider_id]
            if candidates:
                conn.execute("UPDATE model_routes SET candidates_json=? WHERE alias=?", (json.dumps(candidates), route["alias"]))
            else:
                conn.execute("DELETE FROM model_routes WHERE alias=?", (route["alias"],))
        cur = conn.execute("DELETE FROM providers WHERE id=?", (provider_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Provider not found.")
    return {"ok": True}


@admin.post("/providers/{provider_id}/test")
async def test_provider(provider_id: int) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Provider not found.")
    started = time.perf_counter()
    try:
        provider = dict(row)
        provider["api_key"] = decrypt_secret(provider["api_key_enc"])
        provider["custom_headers"] = decode_encrypted_json(provider["custom_headers_enc"], {})
        provider["oauth_config"] = decode_encrypted_json(provider["oauth_config_enc"], {})
        headers = await provider_auth_headers(provider, provider["protocol"])
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=False) as client:
            response = await client.get(model_discovery_url(provider), headers=headers)
        elapsed = int((time.perf_counter() - started) * 1000)
        ok = response.status_code < 400
        note_provider_result(provider_id, ok, response.status_code, elapsed)
        return {"ok": ok, "status": response.status_code, "latency_ms": elapsed,
                "message": "Provider responded to GET /models." if ok else f"Provider returned HTTP {response.status_code}; verify endpoint, credentials, protocol, or whether /models is supported."}
    except (httpx.HTTPError, ValueError, RuntimeError) as exc:
        note_provider_result(provider_id, False, 0, int((time.perf_counter() - started) * 1000))
        return {"ok": False, "status": 0, "latency_ms": int((time.perf_counter() - started) * 1000),
                "message": f"Connection/authentication failed: {type(exc).__name__}. Verify credentials and provider configuration."}


@admin.post("/providers/{provider_id}/oauth/start")
async def start_oauth(provider_id: int) -> dict[str, str]:
    with db() as conn:
        row = conn.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Provider not found.")
    if row["auth_type"] != "oauth2":
        raise HTTPException(400, "Set the provider authentication mode to OAuth 2.0 first.")
    config = decode_encrypted_json(row["oauth_config_enc"], {})
    if not config.get("authorization_url") or not config.get("token_url") or not config.get("client_id"):
        raise HTTPException(400, "Save the OAuth authorization URL, token URL, and client ID first.")
    port = os.getenv("MOSAIC_PORT", "8000")
    redirect_uri = f"http://127.0.0.1:{port}/api/oauth/callback"
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode("ascii")
    now = time.time()
    # Remove expired attempts to keep the in-memory state bounded.
    for key in list(OAUTH_PENDING):
        if OAUTH_PENDING[key].get("expires_at", 0) < now:
            OAUTH_PENDING.pop(key, None)
    OAUTH_PENDING[state] = {"provider_id": provider_id, "verifier": verifier, "redirect_uri": redirect_uri, "expires_at": now + 600}
    query = dict(parse_qsl(urlsplit(config["authorization_url"]).query, keep_blank_values=True))
    query.update({"response_type": "code", "client_id": config["client_id"], "redirect_uri": redirect_uri, "state": state,
                  "code_challenge": challenge, "code_challenge_method": "S256"})
    scopes = config.get("scopes", "").strip()
    if scopes:
        query["scope"] = scopes
    parts = urlsplit(config["authorization_url"])
    auth_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
    return {"authorization_url": auth_url, "redirect_uri": redirect_uri}


@app.get("/api/oauth/callback", response_class=HTMLResponse)
async def oauth_callback(code: str = "", state: str = "", error: str = ""):
    pending = OAUTH_PENDING.pop(state, None) if state else None
    if not pending or pending.get("expires_at", 0) < time.time():
        return HTMLResponse("<h2>MosaicRoute OAuth</h2><p>Login expired or state did not match. Return to MosaicRoute and try Connect again.</p>", status_code=400)
    if error or not code:
        return HTMLResponse("<h2>MosaicRoute OAuth</h2><p>Provider authorization was not completed. Return to MosaicRoute and try again.</p>", status_code=400)
    with db() as conn:
        row = conn.execute("SELECT * FROM providers WHERE id=?", (pending["provider_id"],)).fetchone()
    if not row:
        return HTMLResponse("<h2>MosaicRoute OAuth</h2><p>The provider was removed before login finished.</p>", status_code=404)
    config = decode_encrypted_json(row["oauth_config_enc"], {})
    payload: dict[str, str] = {"grant_type": "authorization_code", "code": code, "redirect_uri": pending["redirect_uri"],
                               "client_id": config.get("client_id", ""), "code_verifier": pending["verifier"]}
    client_secret = config.get("client_secret", "")
    auth_method = config.get("token_auth_method", "client_secret_post")
    headers: dict[str, str] = {"Accept": "application/json"}
    if client_secret and auth_method == "client_secret_basic":
        basic = base64.b64encode(f"{config['client_id']}:{client_secret}".encode()).decode("ascii")
        headers["Authorization"] = f"Basic {basic}"
    elif client_secret and auth_method != "none":
        payload["client_secret"] = client_secret
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
            response = await client.post(config["token_url"], data=payload, headers=headers)
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("OAuth token response did not include access_token")
        token_bundle = {"access_token": access_token, "refresh_token": token_data.get("refresh_token", ""),
                        "token_type": token_data.get("token_type", "Bearer"),
                        "expires_at": time.time() + max(0, int(token_data.get("expires_in", 3600) or 3600))}
        with db() as conn:
            conn.execute("UPDATE providers SET api_key_enc=? WHERE id=?", (encrypt_secret(json.dumps(token_bundle, separators=(",", ":"))), pending["provider_id"]))
        # After a successful standard OAuth exchange, immediately discover the
        # provider catalog so the connection is usable without another manual step.
        discovery = await discover_one_provider(int(pending["provider_id"]))
        if discovery.get("ok"):
            discovery_text = f" Model discovery found {int(discovery.get('count', 0))} model(s)."
        else:
            discovery_text = " Sign-in succeeded, but model discovery needs configuration; return to MosaicRoute and adjust the documented model-list path if necessary."
        return HTMLResponse("<!doctype html><html><head><meta charset='utf-8'><title>MosaicRoute connected</title></head><body style='font:16px system-ui;max-width:640px;margin:15vh auto;padding:24px;color:#172033'><h2>Provider connected</h2><p>OAuth sign-in succeeded. Your tokens are encrypted locally." + discovery_text + "</p><p><a href='/'>Return to MosaicRoute</a></p></body></html>")
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        return HTMLResponse("<h2>MosaicRoute OAuth</h2><p>Token exchange failed. Check the OAuth URLs, client registration, and redirect URI in the provider settings.</p><p><a href='/'>Return to MosaicRoute</a></p>", status_code=502)


@admin.get("/models")
async def list_model_routes() -> list[dict[str, Any]]:
    providers = {row["id"]: row for row in provider_rows()}
    with db() as conn:
        routes = conn.execute("SELECT * FROM model_routes ORDER BY alias COLLATE NOCASE").fetchall()
    result = []
    for route in routes:
        candidates = []
        for candidate in json.loads(route["candidates_json"]):
            provider = providers.get(candidate["provider_id"])
            candidates.append({"provider_id": candidate["provider_id"], "provider_name": provider["name"] if provider else "Missing provider",
                              "protocol": provider["protocol"] if provider else "unknown", "upstream_model": candidate["upstream_model"],
                              "provider_active": bool(provider and provider["is_active"])})
        result.append({"id": route["id"], "alias": route["alias"], "candidates": candidates, "strategy": route["strategy"], "is_auto_generated": bool(route["is_auto_generated"]), "created_at": route["created_at"]})
    return result


@admin.post("/models")
async def save_model_route(payload: ModelInput) -> dict[str, Any]:
    alias = payload.alias.strip()
    if not alias or any(c.isspace() for c in alias) or len(alias) > 120:
        raise HTTPException(400, "Alias must be non-empty and contain no spaces.")
    candidates = []
    with db() as conn:
        for item in payload.candidates:
            model_name = item.upstream_model.strip()
            if not model_name:
                raise HTTPException(400, "Each candidate needs an upstream model name.")
            if len(model_name) > 300 or any(ord(ch) < 32 or ord(ch) == 127 for ch in model_name):
                raise HTTPException(400, "Upstream model IDs cannot contain control characters and must be at most 300 characters.")
            provider = conn.execute("SELECT id FROM providers WHERE id=?", (item.provider_id,)).fetchone()
            if not provider:
                raise HTTPException(400, f"Provider {item.provider_id} does not exist.")
            candidate = {"provider_id": item.provider_id, "upstream_model": model_name}
            if candidate not in candidates:
                candidates.append(candidate)
        conn.execute("INSERT INTO model_routes(alias,candidates_json,strategy,is_auto_generated,created_at) VALUES(?,?,?,?,?) ON CONFLICT(alias) DO UPDATE SET candidates_json=excluded.candidates_json,strategy=excluded.strategy,is_auto_generated=0",
                     (alias, json.dumps(candidates), payload.strategy, 0, utc_now()))
        route = conn.execute("SELECT * FROM model_routes WHERE alias=?", (alias,)).fetchone()
    return {"id": route["id"], "alias": route["alias"], "candidates": candidates, "strategy": route["strategy"]}


@admin.delete("/models/{route_id}")
async def delete_model_route(route_id: int) -> dict[str, bool]:
    with db() as conn:
        cur = conn.execute("DELETE FROM model_routes WHERE id=?", (route_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Model alias not found.")
    return {"ok": True}


@admin.get("/logs")
async def get_logs(limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    with db() as conn:
        rows = conn.execute("SELECT * FROM request_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(row) for row in rows]


@admin.delete("/logs")
async def clear_logs() -> dict[str, bool]:
    with db() as conn:
        conn.execute("DELETE FROM request_logs")
    return {"ok": True}


@admin.get("/dashboard")
async def dashboard() -> dict[str, Any]:
    with db() as conn:
        providers = conn.execute("SELECT COUNT(*) n FROM providers").fetchone()["n"]
        aliases = conn.execute("SELECT COUNT(*) n FROM model_routes").fetchone()["n"]
        today = datetime.now(timezone.utc).date().isoformat()
        stats = conn.execute("SELECT COUNT(*) requests, COALESCE(SUM(prompt_tokens+completion_tokens),0) tokens, SUM(CASE WHEN status_code>=200 AND status_code<400 THEN 1 ELSE 0 END) successes, AVG(latency_ms) avg_latency FROM request_logs WHERE substr(created_at,1,10)=?", (today,)).fetchone()
        total = conn.execute("SELECT COUNT(*) n FROM request_logs").fetchone()["n"]
        failures = conn.execute("SELECT COUNT(*) n FROM request_logs WHERE status_code>=400").fetchone()["n"]
    reqs = stats["requests"] or 0
    return {"providers": providers, "aliases": aliases, "requests_today": reqs, "tokens_today": stats["tokens"] or 0,
            "successes_today": stats["successes"] or 0, "avg_latency_ms": int(stats["avg_latency"] or 0),
            "total_requests": total, "failed_requests": failures, "success_rate_today": round((stats["successes"] or 0) / reqs * 100, 1) if reqs else 0}


@admin.get("/analytics")
async def analytics(window_hours: int = 24) -> dict[str, Any]:
    """Local operational analytics; no prompt/completion text is recorded."""
    hours = max(1, min(int(window_hours), 24 * 90))
    cutoff = (datetime.now(timezone.utc).timestamp() - hours * 3600)
    cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).isoformat(timespec="seconds")
    with db() as conn:
        rows = conn.execute("""SELECT created_at,upstream_provider,upstream_model,latency_ms,status_code,prompt_tokens,completion_tokens
            FROM request_logs WHERE created_at>=? ORDER BY id DESC LIMIT 100000""", (cutoff_iso,)).fetchall()
    records = [dict(row) for row in rows]
    latencies = sorted(max(0, int(r["latency_ms"] or 0)) for r in records if int(r["latency_ms"] or 0) > 0)
    def percentile(q: float) -> int:
        if not latencies: return 0
        index = min(len(latencies) - 1, max(0, int((len(latencies) - 1) * q + 0.999999)))
        return latencies[index]
    by_provider: dict[str, dict[str, Any]] = {}
    by_model: dict[str, dict[str, Any]] = {}
    for r in records:
        ok = 200 <= int(r["status_code"] or 0) < 400
        tokens = int(r["prompt_tokens"] or 0) + int(r["completion_tokens"] or 0)
        for key, value in (("provider", str(r["upstream_provider"] or "unrouted")), ("model", str(r["upstream_model"] or "unrouted"))):
            table = by_provider if key == "provider" else by_model
            item = table.setdefault(value, {"requests": 0, "successes": 0, "failures": 0, "tokens": 0, "latency_ms_total": 0})
            item["requests"] += 1
            item["successes"] += int(ok)
            item["failures"] += int(not ok)
            item["tokens"] += tokens
            item["latency_ms_total"] += max(0, int(r["latency_ms"] or 0))
    for table in (by_provider, by_model):
        for item in table.values():
            item["success_rate"] = round(item["successes"] / item["requests"] * 100, 1) if item["requests"] else 0
            item["avg_latency_ms"] = int(item.pop("latency_ms_total") / item["requests"]) if item["requests"] else 0
    total = len(records)
    successes = sum(1 for r in records if 200 <= int(r["status_code"] or 0) < 400)
    return {"window_hours": hours, "requests": total, "successes": successes, "failures": total - successes,
            "success_rate": round(successes / total * 100, 1) if total else 0,
            "tokens": sum(int(r["prompt_tokens"] or 0) + int(r["completion_tokens"] or 0) for r in records),
            "latency_ms": {"p50": percentile(0.50), "p95": percentile(0.95), "p99": percentile(0.99)},
            "providers": sorted([{"name": name, **v} for name, v in by_provider.items()], key=lambda x: x["requests"], reverse=True),
            "models": sorted([{"name": name, **v} for name, v in by_model.items()], key=lambda x: x["requests"], reverse=True)}




@admin.get("/circuit-breakers")
async def circuit_breakers() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("""
            SELECT p.id, p.name, p.is_active, p.failure_count, p.cooldown_until,
                   COALESCE(m.successes, 0) successes, COALESCE(m.failures, 0) failures, COALESCE(m.avg_latency_ms, 0) avg_latency_ms
            FROM providers p LEFT JOIN provider_metrics m ON m.provider_id = p.id
            ORDER BY p.name COLLATE NOCASE
        """).fetchall()
    now = time.time()
    result = []
    for row in rows:
        d = dict(row)
        cooldown_rem = max(0.0, float(d["cooldown_until"] or 0) - now)
        d["cooldown_remaining_seconds"] = round(cooldown_rem, 1)
        d["status"] = "tripped" if cooldown_rem > 0 else ("inactive" if not d["is_active"] else "healthy")
        result.append(d)
    return result


@admin.get("/backup")
async def export_backup() -> dict[str, Any]:
    with db() as conn:
        providers = [dict(r) for r in conn.execute("SELECT * FROM providers").fetchall()]
        routes = [dict(r) for r in conn.execute("SELECT * FROM model_routes").fetchall()]
    return {"version": APP_VERSION, "exported_at": utc_now(), "providers": providers, "model_routes": routes}


@admin.post("/restore")
async def restore_backup(payload: dict[str, Any]) -> dict[str, Any]:
    providers = payload.get("providers", [])
    routes = payload.get("model_routes", [])
    if not isinstance(providers, list) or not isinstance(routes, list):
        raise HTTPException(400, "Invalid backup format: providers and model_routes must be lists.")
    with db() as conn:
        conn.execute("DELETE FROM providers")
        conn.execute("DELETE FROM model_routes")
        for p in providers:
            conn.execute("""
                INSERT INTO providers(id, name, base_url, models_path, models_path_mode, api_key_enc, protocol, auth_type, auth_header, auth_prefix, custom_headers_enc, oauth_config_enc, is_active, failure_count, cooldown_until, rpm_limit, rpd_limit, tpm_limit, tpd_limit, max_concurrency, created_at)
                VALUES(:id, :name, :base_url, :models_path, :models_path_mode, :api_key_enc, :protocol, :auth_type, :auth_header, :auth_prefix, :custom_headers_enc, :oauth_config_enc, :is_active, :failure_count, :cooldown_until, :rpm_limit, :rpd_limit, :tpm_limit, :tpd_limit, :max_concurrency, :created_at)
            """, p)
        for r in routes:
            conn.execute("""
                INSERT INTO model_routes(id, alias, candidates_json, strategy, is_auto_generated, created_at)
                VALUES(:id, :alias, :candidates_json, :strategy, :is_auto_generated, :created_at)
            """, r)
    return {"ok": True, "restored_providers": len(providers), "restored_routes": len(routes)}

app.include_router(admin)


async def provider_auth_headers(provider: dict[str, Any], protocol: str) -> dict[str, str]:
    headers: dict[str, str] = {"Accept": "application/json", "Content-Type": "application/json"}
    auth_type = provider.get("auth_type", "auto")
    credential = provider.get("api_key", "")
    custom_headers = provider.get("custom_headers", {}) or {}

    if auth_type == "oauth2":
        try:
            token_bundle = json.loads(credential) if credential else {}
        except json.JSONDecodeError:
            token_bundle = {}
        oauth_config = provider.get("oauth_config", {}) or {}
        expires_at = float(token_bundle.get("expires_at", 0) or 0)
        refresh_token = str(token_bundle.get("refresh_token", "") or "")
        if expires_at and expires_at <= time.time() + 60 and refresh_token:
            payload = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": oauth_config.get("client_id", "")}
            client_secret = oauth_config.get("client_secret", "")
            auth_method = oauth_config.get("token_auth_method", "client_secret_post")
            refresh_headers: dict[str, str] = {"Accept": "application/json"}
            if client_secret and auth_method == "client_secret_basic":
                encoded = base64.b64encode(f"{oauth_config.get('client_id','')}:{client_secret}".encode()).decode("ascii")
                refresh_headers["Authorization"] = f"Basic {encoded}"
            elif client_secret and auth_method != "none":
                payload["client_secret"] = client_secret
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
                refresh_response = await client.post(oauth_config["token_url"], data=payload, headers=refresh_headers)
            refresh_response.raise_for_status()
            refreshed = refresh_response.json()
            if not refreshed.get("access_token"):
                raise RuntimeError("OAuth refresh response omitted access_token.")
            token_bundle["access_token"] = refreshed["access_token"]
            token_bundle["refresh_token"] = refreshed.get("refresh_token", refresh_token)
            token_bundle["token_type"] = refreshed.get("token_type", token_bundle.get("token_type", "Bearer"))
            token_bundle["expires_at"] = time.time() + max(0, int(refreshed.get("expires_in", 3600) or 3600))
            with db() as conn:
                conn.execute("UPDATE providers SET api_key_enc=? WHERE id=?", (encrypt_secret(json.dumps(token_bundle, separators=(",", ":"))), provider["id"]))
        credential = str(token_bundle.get("access_token", ""))
        if not credential:
            raise RuntimeError("OAuth provider is not connected. Open provider settings and choose Connect OAuth.")
        auth_type = "bearer"

    if auth_type == "auto":
        if protocol == "anthropic" and credential:
            headers["x-api-key"] = credential
        elif credential:
            headers["Authorization"] = f"Bearer {credential}"
    elif auth_type == "bearer":
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
    elif auth_type == "api_key":
        if credential:
            header_name = provider.get("auth_header") or "x-api-key"
            header_prefix = provider.get("auth_prefix", "")
            headers[header_name] = f"{header_prefix}{credential}"
    elif auth_type == "session":
        if credential:
            header_name = provider.get("auth_header") or "Authorization"
            header_prefix = provider.get("auth_prefix", "Bearer ")
            headers[header_name] = f"{header_prefix}{credential}"
    elif auth_type == "cookie":
        if credential:
            headers["Cookie"] = credential
    elif auth_type == "custom_header":
        if credential:
            header_name = provider.get("auth_header") or "Authorization"
            header_prefix = provider.get("auth_prefix", "")
            headers[header_name] = f"{header_prefix}{credential}"
    elif auth_type in {"none", "custom_headers"}:
        pass

    if protocol == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
    for name, value in custom_headers.items():
        if isinstance(name, str) and isinstance(value, str):
            headers[name] = value
    return headers


async def route_request(request: Request, api_path: str, protocol: str):
    try:
        verify_local_key(request)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"error": {"type": "unauthorized", "message": str(exc.detail)}}
        return JSONResponse(status_code=exc.status_code, content=detail)

    try:
        raw_body = bytearray()
        async for chunk in request.stream():
            if len(raw_body) + len(chunk) > MAX_BODY_BYTES:
                return api_error(413, "request_too_large", "Request body exceeds the 16 MiB local limit.")
            raw_body.extend(chunk)
        body = json.loads(raw_body)
    except Exception:
        return api_error(400, "validation_error", "Request body must be valid JSON.")
    if not isinstance(body, dict) or not body.get("model"):
        return api_error(400, "validation_error", "Expected a JSON object with a model field.")
    if protocol == "openai" and not isinstance(body.get("messages"), list) and api_path == "chat/completions":
        return api_error(400, "validation_error", "Expected a messages array.")
    if protocol == "anthropic" and not isinstance(body.get("messages"), list):
        return api_error(400, "validation_error", "Expected a messages array.")

    if api_path == "embeddings" and not ("input" in body):
        return api_error(400, "validation_error", "Embeddings requests require an input field.")

    alias = str(body["model"])
    session_hash = get_sticky_session_key(request, body, alias, protocol) if api_path in {"chat/completions", "responses", "messages"} else ""
    candidates = resolve_candidates(alias, protocol)
    candidates = order_by_sticky_session(candidates, alias, protocol, session_hash)
    if api_path == "embeddings":
        candidates = safe_embedding_candidates(candidates)
    if not candidates:
        route_type = "OpenAI-compatible" if protocol == "openai" else "Anthropic Messages"
        return api_error(404, "not_found", f"No active {route_type} candidates are configured for alias '{alias}'. Configure a matching provider and model alias in the dashboard.")

    wants_stream = bool(body.get("stream", False))
    started = time.perf_counter()
    last_error = "No candidate could serve the request."
    last_status = 502
    quota_rejections = 0
    quota_retry_after = 0
    estimated_tokens = estimate_request_tokens(body)
    timeout = httpx.Timeout(connect=12.0, read=240.0, write=60.0, pool=12.0)

    for index, candidate in enumerate(candidates):
        outgoing = dict(body)
        outgoing["model"] = candidate["upstream_model"]
        provider_name = candidate["name"]
        upstream_model_name = candidate["upstream_model"]
        candidate_started = time.perf_counter()
        quota = reserve_provider_quota(int(candidate["id"]), upstream_model_name, estimated_tokens)
        if not quota["allowed"]:
            quota_rejections += 1
            quota_retry_after = max(quota_retry_after, int(quota.get("retry_after", 0)))
            last_status = 429
            last_error = f"{provider_name}: {quota['reason']}"
            continue
        quota_event_id = quota.get("event_id")
        # OpenAI Responses and Anthropic Messages are passed through natively.
        endpoint = api_path
        if protocol == "openai" and api_path == "responses":
            endpoint = "responses"
        if protocol == "anthropic":
            endpoint = "messages"
        client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
        try:
            headers = await provider_auth_headers(candidate, protocol)
            headers["Accept"] = "text/event-stream" if wants_stream else "application/json"
            if wants_stream:
                upstream_request = client.build_request("POST", join_upstream(candidate["base_url"], endpoint), json=outgoing, headers=headers)
                upstream_response = await client.send(upstream_request, stream=True)
                status = upstream_response.status_code
                if (status in TRANSIENT_STATUSES or status in {401, 402, 403}) and index < len(candidates) - 1:
                    last_status = status
                    last_error = f"{provider_name} returned HTTP {status}"
                    note_provider_result(candidate["id"], False, status, int((time.perf_counter() - candidate_started) * 1000), upstream_model_name, parse_retry_after(upstream_response.headers.get("retry-after")))
                    finish_quota_event(quota_event_id, status)
                    await upstream_response.aread()
                    await upstream_response.aclose()
                    await client.aclose()
                    continue
                if status >= 400:
                    content = await upstream_response.aread()
                    await upstream_response.aclose()
                    await client.aclose()
                    note_provider_result(candidate["id"], False, status, int((time.perf_counter() - candidate_started) * 1000), upstream_model_name, parse_retry_after(upstream_response.headers.get("retry-after")))
                    finish_quota_event(quota_event_id, status)
                    message = content.decode("utf-8", errors="replace")[:2500]
                    log_request(alias, provider_name, upstream_model_name, started, status, error=f"HTTP {status}")
                    return JSONResponse(status_code=status, content={"error": {"type": "upstream_error", "message": message}})
                note_provider_result(candidate["id"], True, status, int((time.perf_counter() - candidate_started) * 1000), upstream_model_name)
                remember_sticky_session(session_hash, alias, protocol, candidate)
                response_headers = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
                                    "X-MosaicRoute-Provider": safe_response_header_value(candidate["name"]), "X-MosaicRoute-Model": safe_response_header_value(upstream_model_name)}
                if upstream_response.headers.get("x-request-id"):
                    response_headers["x-request-id"] = upstream_response.headers["x-request-id"]
                return StreamingResponse(stream_raw(upstream_response, client, alias=alias, provider=candidate, model=upstream_model_name, started=started, quota_event_id=quota_event_id),
                                         status_code=status, media_type=upstream_response.headers.get("content-type", "text/event-stream"), headers=response_headers)

            response = await client.post(join_upstream(candidate["base_url"], endpoint), json=outgoing, headers=headers)
            await client.aclose()
            status = response.status_code
            if (status in TRANSIENT_STATUSES or status in {401, 402, 403}) and index < len(candidates) - 1:
                last_status = status
                last_error = f"{provider_name} returned HTTP {status}"
                note_provider_result(candidate["id"], False, status, int((time.perf_counter() - candidate_started) * 1000), upstream_model_name, parse_retry_after(response.headers.get("retry-after")))
                finish_quota_event(quota_event_id, status)
                continue
            if status >= 400:
                note_provider_result(candidate["id"], False, status, int((time.perf_counter() - candidate_started) * 1000), upstream_model_name, parse_retry_after(response.headers.get("retry-after")))
                finish_quota_event(quota_event_id, status)
                log_request(alias, provider_name, upstream_model_name, started, status, error=f"HTTP {status}")
                try:
                    payload = response.json()
                except ValueError:
                    payload = {"error": {"type": "upstream_error", "message": response.text[:2500]}}
                return JSONResponse(status_code=status, content=payload)
            if api_path == "audio/speech":
                note_provider_result(candidate["id"], True, status, int((time.perf_counter() - candidate_started) * 1000), upstream_model_name)
                finish_quota_event(quota_event_id, status)
                log_request(alias, provider_name, upstream_model_name, started, status)
                pass_headers = {"X-MosaicRoute-Provider": safe_response_header_value(candidate["name"]), "X-MosaicRoute-Model": safe_response_header_value(upstream_model_name)}
                for header in ("content-disposition", "x-request-id", "cache-control"):
                    if response.headers.get(header):
                        pass_headers[header] = response.headers[header]
                return Response(content=response.content, status_code=status,
                                media_type=response.headers.get("content-type", "audio/mpeg"), headers=pass_headers)
            try:
                result = response.json()
            except ValueError:
                note_provider_result(candidate["id"], False, status, int((time.perf_counter() - candidate_started) * 1000), upstream_model_name, parse_retry_after(response.headers.get("retry-after")))
                finish_quota_event(quota_event_id, 502)
                log_request(alias, provider_name, upstream_model_name, started, 502, error="Provider returned non-JSON response")
                return api_error(502, "upstream_error", "Provider returned a non-JSON response.")
            note_provider_result(candidate["id"], True, status, int((time.perf_counter() - candidate_started) * 1000), upstream_model_name)
            remember_sticky_session(session_hash, alias, protocol, candidate)
            usage = extract_usage(result)
            finish_quota_event(quota_event_id, status, usage_token_count(usage))
            log_request(alias, provider_name, upstream_model_name, started, status, usage=usage)
            return JSONResponse(status_code=status, content=result, headers={
                "X-MosaicRoute-Provider": str(candidate["name"]), "X-MosaicRoute-Model": upstream_model_name
            })
        except httpx.HTTPError as exc:
            last_status = 502
            last_error = f"{provider_name}: {type(exc).__name__}"
            note_provider_result(candidate["id"], False, 0, int((time.perf_counter() - candidate_started) * 1000), upstream_model_name)
            finish_quota_event(quota_event_id, 0)
            try:
                await client.aclose()
            except Exception:
                pass
            if index < len(candidates) - 1:
                continue
        except Exception as exc:
            last_status = 502
            last_error = f"{provider_name}: {type(exc).__name__}"
            finish_quota_event(quota_event_id, 0)
            try:
                await client.aclose()
            except Exception:
                pass
            if index < len(candidates) - 1:
                continue

    log_request(alias, "", "", started, last_status, error=last_error)
    if quota_rejections == len(candidates):
        return JSONResponse(status_code=429, headers={"Retry-After": str(quota_retry_after)} if quota_retry_after else {},
                            content={"error": {"type": "rate_limit_exceeded", "message": f"All candidates are at configured quota limits. {last_error}"}})
    return api_error(502, "upstream_unavailable", f"All configured candidates failed. Last issue: {last_error}")


@app.get("/v1/models")
async def openai_models(request: Request):
    try:
        verify_local_key(request)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"error": {"type": "unauthorized", "message": str(exc.detail)}}
        return JSONResponse(status_code=exc.status_code, content=detail)
    with db() as conn:
        rows = conn.execute("SELECT alias,created_at FROM model_routes ORDER BY alias COLLATE NOCASE").fetchall()
        discovered = conn.execute("""SELECT DISTINCT dm.model_id,p.name provider_name,dm.discovered_at
            FROM discovered_models dm JOIN providers p ON p.id=dm.provider_id
            WHERE dm.is_available=1 AND p.is_active=1 ORDER BY dm.model_id COLLATE NOCASE""").fetchall()
    data = [{"id": row["alias"], "object": "model", "created": int(datetime.fromisoformat(row["created_at"]).timestamp()), "owned_by": "mosaicroute"} for row in rows]
    known={item["id"] for item in data}
    for row in discovered:
        if row["model_id"] in known: continue
        data.append({"id":row["model_id"],"object":"model","created":int(datetime.fromisoformat(row["discovered_at"]).timestamp()),"owned_by":row["provider_name"]})
        known.add(row["model_id"])
    return {"object": "list", "data": data}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    return await route_request(request, "chat/completions", "openai")


@app.post("/v1/completions")
async def text_completions(request: Request):
    return await route_request(request, "completions", "openai")


@app.post("/v1/embeddings")
async def embeddings(request: Request):
    return await route_request(request, "embeddings", "openai")


@app.post("/v1/moderations")
async def moderations(request: Request):
    return await route_request(request, "moderations", "openai")


@app.post("/v1/images/generations")
async def image_generations(request: Request):
    return await route_request(request, "images/generations", "openai")


@app.post("/v1/audio/speech")
async def audio_speech(request: Request):
    return await route_request(request, "audio/speech", "openai")


@app.post("/v1/responses")
async def responses(request: Request):
    return await route_request(request, "responses", "openai")


@app.post("/v1/messages")
async def anthropic_messages(request: Request):
    return await route_request(request, "messages", "anthropic")

@app.get("/v1/models/{model_id:path}")
async def openai_model_detail(request: Request, model_id: str):
    try:
        verify_local_key(request)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"error": {"type": "unauthorized", "message": str(exc.detail)}}
        return JSONResponse(status_code=exc.status_code, content=detail)
    with db() as conn:
        route = conn.execute("SELECT alias,created_at FROM model_routes WHERE alias = ?", (model_id,)).fetchone()
        discovered = conn.execute("""SELECT dm.model_id,p.name provider_name,dm.discovered_at,dm.metadata_json
            FROM discovered_models dm JOIN providers p ON p.id=dm.provider_id
            WHERE dm.model_id=? AND dm.is_available=1 AND p.is_active=1 LIMIT 1""", (model_id,)).fetchone()
    if route:
        return {"id": route["alias"], "object": "model", "created": int(datetime.fromisoformat(route["created_at"]).timestamp()), "owned_by": "mosaicroute"}
    if discovered:
        return {"id": discovered["model_id"], "object": "model", "created": int(datetime.fromisoformat(discovered["discovered_at"]).timestamp()), "owned_by": discovered["provider_name"], "metadata": json.loads(discovered["metadata_json"] or "{}")}
    return api_error(404, "not_found", f"Model '{model_id}' not found.")


@app.post("/v1/audio/transcriptions")
async def audio_transcriptions(request: Request):
    return await route_request(request, "audio/transcriptions", "openai")



# Static assets are mounted before the direct-run entry point so `python app.py`
# and the packaged Windows launcher both serve the dashboard correctly.
from fastapi.staticfiles import StaticFiles
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

if __name__ == "__main__":
    import uvicorn
    if FIRST_RUN:
        print("\nWelcome to MosaicRoute — first-run credentials\n")
        print(f"Dashboard:       http://127.0.0.1:{os.getenv('MOSAIC_PORT', '8000')}/")
        print(f"Dashboard pass:  {ADMIN_PASSWORD}")
        print(f"Local API key:   {LOCAL_API_KEY}")
        print("Credentials are also saved in .env; keep it private.\n")
    host = os.getenv("MOSAIC_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        print("WARNING: MosaicRoute is configured to listen beyond loopback. Only do this behind strong authentication and a trusted network.")
    uvicorn.run(app, host=host, port=int(os.getenv("MOSAIC_PORT", "8000")), log_level="info")
