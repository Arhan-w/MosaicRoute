# MosaicRoute Roadmap

## Implemented in v0.6.0

- JSON-backed provider preset registry, auto-discovery, adaptive model routes and health-aware failover.
- Optional provider/model quota limits with a local reservation ledger.
- Sticky conversation routing and p50/p95/p99 analytics.
- Compatible routes for chat, responses, messages, completions, embeddings, moderation, image generation and audio speech.

## Remaining parity work

- Provider-specific native adapters for non-standard discovery/auth/wire formats (not only generic-compatible presets).
- A locally verifiable signed provider/model catalog update mechanism.
- Device-code OAuth and official vendor CLI integrations where supported and permitted.
- Native search, transcription, video, MCP and cloud-agent integrations.
- Stronger integration benchmarking across supported providers and a packaging smoke test on Windows/macOS/Linux.

