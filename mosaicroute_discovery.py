"""Standalone helpers for normalizing and ranking discovered model catalogs.

The main application owns persistence, credentials, refresh scheduling, and API
routes. This module can be used by scripts or adapter tests without opening the
MosaicRoute database.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx


@dataclass
class DiscoveredModel:
    id: str
    provider: str
    source: str = "models_endpoint"
    capabilities: list[str] = field(default_factory=list)
    context_window: int | None = None
    input_cost: float | None = None
    output_cost: float | None = None
    observed_latency_ms: float | None = None
    success_rate: float = 0.5
    last_seen: float = field(default_factory=time.time)


def _rows(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "models", "results", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _rows(value)
            if nested:
                return nested
    return []


def normalize_model_catalog(payload: Any, provider: str) -> list[DiscoveredModel]:
    models: dict[str, DiscoveredModel] = {}
    for row in _rows(payload):
        if isinstance(row, str):
            model_id, meta = row.strip(), {}
        elif isinstance(row, dict):
            model_id = str(row.get("id") or row.get("model") or row.get("model_id") or row.get("name") or "").strip()
            meta = row
        else:
            continue
        if model_id.startswith("models/"):
            model_id = model_id[7:]
        if not model_id or len(model_id) > 300 or any(ord(char) < 32 for char in model_id):
            continue
        pricing = meta.get("pricing") if isinstance(meta.get("pricing"), dict) else {}
        def number(*values: Any) -> float | None:
            for value in values:
                try:
                    if value is not None and value != "":
                        result = float(value)
                        if result >= 0:
                            return result
                except (ValueError, TypeError):
                    continue
            return None
        capabilities = meta.get("capabilities") or meta.get("supported_parameters") or []
        if isinstance(capabilities, str):
            capabilities = [capabilities]
        elif not isinstance(capabilities, list):
            capabilities = []
        models.setdefault(model_id, DiscoveredModel(
            id=model_id,
            provider=provider,
            capabilities=[str(value) for value in capabilities if isinstance(value, (str, int, float))],
            context_window=number(meta.get("context_window"), meta.get("context_length")),
            input_cost=number(meta.get("input_cost"), meta.get("prompt_cost"), pricing.get("prompt"), pricing.get("input")),
            output_cost=number(meta.get("output_cost"), meta.get("completion_cost"), pricing.get("completion"), pricing.get("output")),
        ))
    return list(models.values())


class ModelDiscovery:
    """HTTP model catalog discovery with a short in-memory cache."""
    def __init__(self, ttl_seconds: int = 900):
        self.ttl_seconds = max(0, ttl_seconds)
        self._cache: dict[str, tuple[float, list[DiscoveredModel]]] = {}

    async def discover(
        self,
        provider: str,
        base_url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 12.0,
        models_path: str = "/models",
        path_mode: str = "base",
    ) -> list[DiscoveredModel]:
        base = urlsplit(base_url)
        if base.scheme not in {"http", "https"} or not base.hostname:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        if not models_path.startswith("/") or ".." in models_path.split("/") or any(x in models_path for x in "?#\\"):
            raise ValueError("models_path must be an absolute safe path without query or fragment")
        if path_mode not in {"base", "host"}:
            raise ValueError("path_mode must be 'base' or 'host'")
        url = (urlunsplit((base.scheme, base.netloc, models_path, "", "")) if path_mode == "host"
               else base_url.rstrip("/") + "/" + models_path.lstrip("/"))
        cache_key = provider + "|" + url + "|" + str(sorted((headers or {}).items()))
        cached = self._cache.get(cache_key)
        if cached and self.ttl_seconds and time.time() - cached[0] < self.ttl_seconds:
            return cached[1]

        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.get(url, headers=headers or {})
        latency = (time.perf_counter() - started) * 1000
        response.raise_for_status()
        models = normalize_model_catalog(response.json(), provider)
        for model in models:
            model.observed_latency_ms = latency
        self._cache[cache_key] = (time.time(), models)
        return models

    def rank(self, models: list[DiscoveredModel], intent: str = "general") -> list[DiscoveredModel]:
        intent = intent.lower()
        def score(model: DiscoveredModel) -> tuple[float, float, float, float]:
            latency = model.observed_latency_ms if model.observed_latency_ms is not None else 1e9
            has_price = model.input_cost is not None and model.output_cost is not None
            cost = (model.input_cost or 0) + (model.output_cost or 0) if has_price else 1e9
            coding_hint = any(re.search(r"(^|[-_/])(code|coder|coding|devstral)([-_/]|$)", model.id.lower()) for _ in [0])
            if intent == "coding":
                return (0 if coding_hint else 1, -model.success_rate, latency, cost)
            if intent == "fast":
                return (latency, -model.success_rate, cost, -float(model.context_window or 0))
            if intent in {"cheap", "free"}:
                return (cost if has_price else 1e9, -model.success_rate, latency, -float(model.context_window or 0))
            return (-model.success_rate, latency, cost, -float(model.context_window or 0))
        return sorted(models, key=score)
