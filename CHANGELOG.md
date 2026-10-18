# Changelog

## 0.6.0 — Quota-aware multimodal routing, analytics and sticky sessions

- Added optional provider and model-level RPM/RPD/TPM/TPD/concurrency guardrails backed by atomic SQLite quota reservations and a local usage ledger.
- Added quota-inspection endpoint and provider-card quota UI.
- Added pass-through routes for legacy completions, embeddings, moderation, image generation and audio speech; binary audio is preserved instead of incorrectly parsed as JSON.
- Added conservative embeddings fallback: unknown or mismatched vector-space dimensions do not silently switch to a different embedding model.
- Added caller-controlled sticky sessions for chat/Responses/Messages. Raw session IDs are never stored; only a keyed SHA-256 digest is persisted with a bounded TTL.
- Added request analytics with p50/p95/p99 latency, success rate, tokens and provider/model breakdowns.
- Added `X-MosaicRoute-Provider` and `X-MosaicRoute-Model` response headers for successful routed requests.
- Improved model selection with cheapest-known-price and round-robin strategies; model-level observations inform adaptive ordering.
- Moved built-in provider presets to `config/provider_catalog.json` as the editable source of truth, with schema checks at startup.
- Improved Retry-After parsing and provider cooldown behavior.
- Expanded test coverage for quotas, multi-modal compatible endpoints, embeddings safety, round-robin, sticky sessions, analytics, and catalog integrity.

## 0.5.0 — Automatic catalog refresh and generated routes

- Added persistent discovered-model catalog and direct model-ID routing through `/v1/models`.
- Added per-provider, refresh-all, and on-save model discovery with configurable discovery paths and host-root paths.
- Added a scheduled startup/background catalog refresh (30-minute default, configurable with `MOSAIC_DISCOVERY_INTERVAL_SECONDS`; `0` disables it).
- Auto-refresh now creates/updates generated `auto` aliases and removes stale generated aliases without deleting manually authored routes.
- Added `auto/coding`, `auto/fast`, `auto/cheap`, `auto/free`, `auto/offline`, and `auto-anthropic` candidates where metadata supports them.
- Free routing only uses explicit free-style model IDs or explicit zero-cost metadata; unknown pricing is never silently treated as zero.
- Expanded provider catalog to 71 presets, with current caveats and access notes instead of promises that all tiers are free.
- Fixed model-list URL construction and provider-specific host-root discovery paths.
- Added tests for host-root paths, custom paths, cookie-authenticated discovery, refresh status, free-route hints, direct routing and fallback.

## 0.4.0 — Discovery integration

- Added provider discovery API routes and local SQLite model-catalog persistence.
- Exposed discovered model IDs through `/v1/models` and made them directly routable.
- Added smart route generation from discovered model metadata.

## 0.2.0 — Authentication and routing

- Expanded provider templates for official APIs, aggregators, cloud endpoints, and local runtimes.
- Added selectable auth modes: auto, none, Bearer, custom API-key header, session/access token, manual Cookie header, custom credential header, arbitrary custom headers, and OAuth 2.0 authorization-code + PKCE.
- Added encrypted OAuth token bundles, state validation, refresh-token support, and callback handling.
- Added header-injection checks, OAuth URL scheme checks, and safe omission of credential material from dashboard responses.
- Added provider health metrics and Balanced / Most Reliable / Fastest Observed / Manual Priority routing strategies.
- Added fallback for transient upstream errors and configurable candidate ordering.

## 0.1.0 — Initial local-first release

- Added dashboard authentication and encrypted provider credentials.
- Added OpenAI-compatible Chat Completions and Responses pass-through.
- Added Anthropic Messages pass-through, model aliases, ordered fallback, and provider cooldowns.
- Added usage metadata logs, playground, local setup scripts, test suite, and GitHub release workflow.
