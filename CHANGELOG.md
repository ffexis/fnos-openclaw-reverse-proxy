# Changelog

All notable changes to this project will be documented in this file.

Versioning follows [Semantic Versioning](https://semver.org/):
- **MAJOR**: Breaking changes to API or behavior
- **MINOR**: New features or backward-compatible behavioral changes
- **PATCH**: Bug fixes

## [1.2.0] - 2026-07-30

### Added
- `GET /v1/__probe` availability probe endpoint — responds directly without forwarding to OpenClaw gateway, no audit logging (`979827d`)
- Upstream gateway health monitoring — periodically polls OpenClaw `/health` endpoint, maintains internal alive state (`252841d`)
- Fast-fail on proxy routes — returns 502 immediately when gateway is detected as down, instead of waiting for connect timeout (`252841d`)
- Web UI displays real-time gateway status (Online/Offline) in Upstream card (`252841d`)
- `/v1/__probe` and `/health` now report `upstream_alive` field (`252841d`)

### Changed
- `/health` endpoint now requires Bearer token authentication (`3c526a2`)

## [1.1.0] - 2026-07-14

### Changed
- Identity tag injection moved from system message to user message content — OpenClaw strips system messages, so the tag is now prepended to the first user message to ensure reliable identity propagation (`5d76764`)

## [1.0.0] - 2026-07-13

### Added
- Core reverse proxy: forwards `/v1/*` and `/app/*` to OpenClaw gateway with upstream token injection
- Bearer token authentication with token management API (create/delete/regenerate)
- Session key isolation per token with auto-rotation on new conversations
- System prompt stripping and identity injection
- Audit logging (JSONL by date/user, SSE stream parsing to readable text)
- Audit stats API and log download/delete
- Config polling — auto-detects `openclaw.json` changes (port/token)
- IPv6 listening toggle
- Dark-themed Web UI with token management and audit stats
- Admin token protection (cannot be deleted)
