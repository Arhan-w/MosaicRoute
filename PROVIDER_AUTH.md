# Provider authentication and compatibility guide

MosaicRoute uses a single local router with configurable credentials. An authentication method controls which headers are sent; it does not automatically implement every upstream provider's private protocol.

## Authentication modes

| Mode | Request behavior | Notes |
|---|---|---|
| Auto | `Authorization: Bearer …` for OpenAI-compatible endpoints; `x-api-key` for Anthropic Messages | Best default for most API providers. |
| No auth | No credential header added by the auth mode | For local servers and endpoints that explicitly permit anonymous access. |
| Bearer token | `Authorization: Bearer <credential>` | Standard API key or access token. |
| API-key header | `<configured header>: <prefix><credential>` | Supports `x-api-key`, `api-key`, `x-goog-api-key`, `Authorization: Token …`, and similar documented schemes. The prefix is literal, including any trailing space. |
| Session/access token | `<configured header>: <prefix><credential>` | A manually supplied token for an endpoint that documents this form. It does not automate a browser login. |
| Cookie | `Cookie: <manually supplied cookie header>` | Use only with an endpoint you are authorized to access that documents cookie authentication. Cookie values are credentials. |
| Custom credential header | One user-defined header containing the secret | For APIs whose credential header is not a common default. |
| Custom headers JSON | Arbitrary documented header names and values, stored encrypted | Useful for attribution, tenant, API-version, and gateway-specific headers. Dangerous hop-by-hop headers are rejected. |
| OAuth 2.0 + PKCE | Authorization-code flow through the user's browser, then encrypted token storage and refresh where provided | Requires a legitimate OAuth app/client and support for a loopback redirect. It is not a generic replacement for each provider's private CLI or subscription-login flow. |

API keys, manual session tokens, cookies, extra headers, OAuth client secrets, access tokens, and refresh tokens are encrypted at rest using a local Fernet key. Do not share the database without the matching encryption key, and do not share either file if it contains your configuration.

## What provider integration means here

MosaicRoute ships 71 endpoint templates and a custom endpoint option. Templates are convenience defaults. They do not imply that a provider has been live-tested, that a specific model is free, or that all of that provider's features are supported. Set the exact model ID and use **Test connection** and the Playground with your own account before relying on a route.

The 71 presets include common official APIs, inference services, cloud gateways, providers with publicized free routes, and local model servers. Access notes are deliberately phrased as caveats: actual free eligibility is account-, model-, time-, and quota-dependent. A failed or unavailable model-list endpoint can be configured with the path and path-mode fields; not every provider exposes a catalog over the same protocol. A background refresh runs at startup and every 30 minutes by default; set `MOSAIC_DISCOVERY_INTERVAL_SECONDS=0` to disable scheduled refresh. Manual discovery and provider-save discovery are independent of that timer.

The initial request adapters cover:

- OpenAI-compatible Chat Completions, including streaming pass-through.
- OpenAI Responses pass-through for compatible upstreams.
- Anthropic Messages pass-through for configured Anthropic-compatible upstreams.
- Generic headers and auth for compatible upstreams.
- Automatic discovery from common model-list response shapes, direct calls to discovered model IDs, local persistence, scheduled refresh, and smart alias generation based on explicit metadata and observed health/latency.

The project does not yet reproduce all native OmniRoute or FreeLLMAPI adapters. Those projects include provider-specific OAuth/client sign-in, website-cookie integrations, cloud IAM flows, search/audio/image/video interfaces, and custom request/response mappings. MosaicRoute supports standards-based OAuth 2.0 authorization-code + PKCE only when the provider allows a third-party client and the configured callback; it does not include vendor-specific device flows or import an IDE/CLI subscription session automatically. A base URL and a cookie are not sufficient for a service whose web-chat protocol differs from OpenAI or Anthropic. Add a small, documented adapter for such a service rather than guessing its private endpoints.

## Auth/session safety

- No browser profiles are scanned and no cookie database is read.
- Do not paste cookies copied from a browser unless the service explicitly documents that use and it is permitted for your account. Prefer official API keys or OAuth applications.
- Use OAuth only with authorization/token endpoints published by the provider. Register the exact redirect URI that MosaicRoute displays.
- Do not use these fields to share, resell, or bypass another person's subscription or account controls.
- Remote provider endpoints should use HTTPS. Plain HTTP is limited to loopback/private-LAN endpoints for local runtimes.

## Reference projects reviewed

- [OmniRoute](https://github.com/diegosouzapw/OmniRoute) — broad provider registry categories and custom handling for API keys, OAuth, web cookies, local endpoints, and other services.
- [FreeLLMAPI](https://github.com/tashfeenahmed/freellmapi) — a central OpenAI-compatible gateway, model/provider catalog, rate-limit-aware key selection, cooldowns, and fallback.
- [9Router](https://github.com/decolua/9router) — a local-first coding-router user experience.

Official endpoint documentation used when reviewing presets includes [DeepInfra](https://github.com/deepinfra/docs/blob/main/quickstart.mdx), [Featherless](https://featherless.ai/docs/api-overview-and-common-options), [SambaNova Cloud](https://cloud.sambanova.ai/dashboard), [Nebius AI Studio](https://api.studio.nebius.com/docs), [Pollinations](https://gen.pollinations.ai/docs), and [Alibaba Model Studio](https://www.alibabacloud.com/help/en/model-studio/base-url). Provider base URLs, model IDs, region requirements, quotas and auth schemes can change; refer to these official sources before using a preset.

MosaicRoute's implementation is independently written and intentionally scoped to a local, single-user gateway. It borrows general design ideas, not their private credentials, user sessions, or source code.


## Version 0.6 additions

- Optional provider-level RPM/RPD/TPM/TPD and concurrency caps are configurable from the provider form; model-level limits are used only if advertised by the discovery endpoint. See `/api/providers/{id}/quota` for the local ledger.
- A caller can set `X-MosaicRoute-Session` to keep a chat session on the same eligible upstream for the TTL configured by `MOSAIC_STICKY_SESSION_TTL_SECONDS`. Raw IDs are not persisted.
- Credential modes remain explicit: manual session tokens and cookie values are treated as secrets you supply for endpoints authorized to use them; no browser-profile scraping or hidden subscription-session reuse is performed.
