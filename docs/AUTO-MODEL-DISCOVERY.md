# Automatic model discovery and adaptive routing

MosaicRoute v0.6.0 discovers models advertised by provider endpoints and exposes them through a local catalog. It is designed for OpenAI-compatible model-list endpoints and Anthropic-compatible endpoints that implement model listing. It does not claim one-to-one parity with OmniRoute's provider-specific registry or FreeLLMAPI's custom adapters.

## Discovery lifecycle

1. Add a provider using a built-in preset or custom endpoint. Set the protocol, authentication mode, and any documented model-list path.
2. On save, the dashboard immediately attempts discovery. Use **Discover models** on a provider card for one provider, or **Discover all providers** to refresh all active connections.
3. On startup and every 30 minutes by default, a background worker refreshes enabled providers with bounded concurrency. Set `MOSAIC_DISCOVERY_INTERVAL_SECONDS=0` to disable the background worker, or choose another interval up to 86400 seconds.
4. Successful model-list responses are normalized and stored in the local SQLite database. A failed refresh does not erase the last successful catalog. After a successful bulk refresh, generated routes are updated conservatively.
5. Discovered model IDs are available from `GET /v1/models` and can be called directly without an alias. Same-ID providers can form a fallback pool only when their configured wire protocol matches.
6. **Build smart aliases** creates or refreshes `auto`, `auto/coding`, `auto/fast`, `auto/cheap`, `auto/free`, `auto/offline`, `auto/local`, `auto/reliable`, `auto/vision`, `auto/long-context`, `auto/embedding`, and `auto-anthropic` where suitable candidates exist. `auto/free` only considers IDs with explicit free-style markers or explicit zero-price metadata; provider plans and access can still change.

## Model discovery paths

The default discovery URL is `{base_url}/models`, so a base URL such as `https://provider.example/v1` resolves to `https://provider.example/v1/models`. Set a custom path in provider settings when the API documents a different catalog path. Use **Host root** mode only when the documented model-list path starts at the origin root (for example `/api/gateway/models`). Query strings are intentionally not accepted in the path field; use a provider's standard catalog endpoint or a dedicated adapter for APIs that require fixed query parameters or special pagination.

The catalog parser recognizes common response shapes such as `data`, `models`, `results`, and `items`, plus common metadata fields for context, capabilities, modality, and price. For paginated APIs, the discovery engine follows supported next links, cursors, and `has_more`/`last_id` pagination for at most `MOSAIC_DISCOVERY_MAX_PAGES` pages (default 10; maximum 100). Every next-page URL must keep the exact scheme and host of the configured provider; credentials are not forwarded to a cross-origin URL. If refresh fails, the prior successful catalog is retained. The cap prevents unbounded provider requests; set a lower value for slow or rate-limited services. Discovery is metadata-only: MosaicRoute does not send chat prompts during discovery and does not infer a model is free just because the provider has a free tier.

## Adaptive routing

- `auto`: route candidates with observed provider success and latency history, with a preference for healthy endpoints.
- `auto/coding`: prioritizes discovered model IDs whose names suggest coding models; this is a heuristic, not a benchmark.
- `auto/fast`: prioritizes observed latency.
- `auto/cheap`: prioritizes known pricing metadata and then reliability.
- `auto/free`: prioritizes explicit free-like IDs or provider-reported zero pricing. This is not a guarantee that a request cannot incur charges if the provider changes its terms or metadata.
- `auto/offline`: prefers local/private-host models.
- `auto-anthropic`: uses Anthropic-protocol candidates where available.

Observed metrics are local request-history data. New providers start as unmeasured; MosaicRoute does not fabricate quality scores or claim a universally best model.

## Authentication modes

- **Auto / Bearer / API-key header**: official key-based APIs and compatible gateways.
- **Session/access token**: a token you manually supply in a configured header and prefix, where the upstream documents that scheme.
- **Cookie header**: a manually pasted cookie string for an endpoint you are authorized to access, only where that endpoint officially supports cookie auth.
- **Custom header map**: additional documented headers such as API version, client attribution, tenant ID, or referer/title.
- **OAuth 2.0 authorization-code + PKCE**: a generic standards-based browser flow where the provider supports third-party clients and the configured localhost redirect URI. Refresh tokens are used when issued.
- **No auth / local endpoint**: localhost, private-LAN endpoints, or upstreams explicitly designed for anonymous access.

This release does not implement generic device-code OAuth, automatic browser profile/cookie extraction, or private sign-in flows for proprietary tools (for example IDE subscription sessions). Such integrations need a provider-specific adapter that implements the official or authorized flow; a cookie/header field alone does not implement a different web-chat protocol.

## Free provider catalog

The preset catalog is a convenience list of common official APIs, aggregators that may expose free routes, cloud inference, regional APIs, and local runtimes. Every preset is annotated with an access note and a free-tier status. These labels are guidance, not a real-time guarantee. Model lists and quotas may require an account, a verification step, or a key; some gateways require a custom model-list path or do not expose model listing at all.

Free-tier claims and model IDs age quickly. Verify the upstream provider's current pricing, model availability, data policies, and rate limits. Do not assume a free fallback will remain free unless its current route and provider terms confirm that. MosaicRoute does not bypass quotas, payments, account restrictions, or service terms.

## Security notes

Credentials are stored encrypted locally. MosaicRoute does not read browser profiles, copy browser credential databases, or automatically reuse a logged-in consumer subscription. Remote provider endpoints should use HTTPS; plain HTTP is restricted to loopback/private-LAN local providers. Leave the router bound to `127.0.0.1` and do not port-forward the admin interface.

## Routing guardrails and continuity

MosaicRoute can optionally enforce provider-level RPM/RPD/TPM/TPD/concurrency limits configured on a provider. Model metadata may contribute model-level limits when an upstream advertises them. Reservations are stored in a local SQLite ledger; token counts are estimated before dispatch and reconciled against upstream usage when it is present. This is a local safety policy, not a guarantee that an upstream will never change or apply additional limits.

Use `X-MosaicRoute-Session: <stable-id>` (or `X-Session-ID`) on chat, Responses, and Messages requests to enable sticky routing. MosaicRoute hashes the caller-supplied ID with the local API key and stores only the hash, alias, provider/model assignment and expiration. Default TTL is 30 minutes; set `MOSAIC_STICKY_SESSION_TTL_SECONDS=0` to disable. Sticky routing still falls back when the pinned provider is unavailable or quota-blocked.

The local dashboard's `GET /api/analytics?window_hours=24` endpoint reports request counts, success rate, tokens and p50/p95/p99 latency over up to 90 days, with provider/model breakdowns.
