# MosaicRoute v0.6.0

Date: 2026-10-18

MosaicRoute is a local-first personal AI gateway with an editable JSON provider catalog, automatic model discovery, adaptive routing, quota guardrails, sticky sessions, local analytics, and compatible endpoint pass-through.

## Highlights

- **71 configurable provider presets** in `config/provider_catalog.json`, now loaded as the catalog source of truth instead of being hardcoded in Python.
- **Automatic model discovery** when a provider is saved, OAuth connects, on demand, and on a configurable background schedule. Multi-page model catalogs are followed with a configurable page cap; pagination links must remain on the provider origin before credentials are forwarded. Discovery persists model IDs/metadata and updates generated aliases while preserving manually authored routes.
- **Smart routing profiles:** `auto`, `auto/coding`, `auto/fast`, `auto/cheap`, `auto/free`, `auto/offline`, `auto/local`, `auto/reliable`, `auto/vision`, `auto/long-context`, `auto/embedding`, and `auto-anthropic`, when suitable model metadata exists.
- **Quota-aware routing:** optional RPM/RPD/TPM/TPD/concurrency caps, atomic reservation ledger, estimates reconciled with actual upstream usage where available, provider/model metadata limits, and `Retry-After`-aware cooldowns.
- **Sticky sessions:** caller-supplied `X-MosaicRoute-Session` or `X-Session-ID` keeps conversations on an eligible provider/model. The raw session ID is not stored.
- **Local analytics:** `/api/analytics` returns p50/p95/p99 latency, success rate, tokens and per-provider/per-model summaries; quota inspection is available at `/api/providers/{id}/quota`.
- **Broader compatible API surface:** Chat Completions, legacy Completions, Responses, Anthropic Messages, Embeddings, Moderations, Images Generations, and Audio Speech pass-through. Embeddings failover is conservative to avoid switching vector spaces silently; audio response bytes are preserved.
- **Client observability:** successful inference responses return `X-MosaicRoute-Provider` and `X-MosaicRoute-Model`.
- **Security hardening:** remote plain HTTP remains disallowed, credentials remain encrypted at rest, logs remain metadata-only, admin APIs remain session-protected, and custom header validation is enforced.

## Validation

- `python -m pytest -q`: 51 passed.
- `python -m py_compile app.py mosaicroute_discovery.py`: passed.
- `node --check static/app.js`: passed.
- Tests use a mock upstream and do not spend provider credits.

## Compatibility and cost notes

MosaicRoute is not a one-to-one replacement for OmniRoute or FreeLLMAPI. This release offers a broad configurable preset catalog and common compatible APIs; providers that use proprietary OAuth, IDE/CLI subscription sessions, private web-chat APIs, custom media APIs or unusual discovery protocols need dedicated adapters. No automated browser-cookie extraction is performed. Free-tier labels are hints based on available metadata, not billing guarantees.

## Install

Extract the ZIP; run `install.bat` and `start.bat` on Windows, or `./install.sh` and `./start.sh` on macOS/Linux. Open `http://127.0.0.1:8000`.
