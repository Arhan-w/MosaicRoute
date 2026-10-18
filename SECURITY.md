# Security policy

MosaicRoute is designed for personal use on a trusted machine.

## Supported configuration

- Keep the HTTP listener bound to `127.0.0.1` (the default).
- Protect `.env`, `data/mosaicroute.sqlite3`, and `data/.encryption_key` with the same care as provider credentials.
- Use only upstream endpoints you trust and are authorized to access.
- Never publish logs or config files without inspecting them first.

## Reporting

Please open a private security advisory in the GitHub repository once one exists. Do not post API keys, access tokens, prompts, or other secrets in an issue.

## Scope note

MosaicRoute is not hardened as a multi-user or Internet-facing service. The dashboard session store is in-memory and local-only. Do not expose it behind a public reverse proxy without a separate security review, proper TLS, stronger session persistence and access control, rate limiting, and a threat model.
