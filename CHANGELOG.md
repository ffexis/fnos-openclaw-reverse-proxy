# Changelog

All notable changes to this project will be documented in this file.

Versioning follows [Semantic Versioning](https://semver.org/):
- **MAJOR**: Breaking changes to API or behavior
- **MINOR**: New features or backward-compatible behavioral changes
- **PATCH**: Bug fixes

## [1.4.0] - 2026-10-18

### Added
- Control Panel header tabs — existing content moved under the **Proxy** tab unchanged; new **Magpie** tab shows the magpie gateway usage/quota dashboard
- Read-only magpie proxy API: `GET /api/magpie/usage`, `GET /api/magpie/quotas`, `GET /api/magpie/usage/requests`, `GET /api/magpie/quotas/history` — all behind `require_auth`, query params (`period`/`limit`) passed through to magpie
- Magpie dashboard cards: per-provider quota windows with remaining/reset countdown bars, 30-day usage summary with daily-calls sparkline, recent requests table with limit selector (20/50/100) and manual refresh + last-fetched timestamp
- New `MAGPIE_URL` environment variable (default `http://127.0.0.1:3425`) — magpie upstream address, not hard-coded in routes
- Fault tolerance: magpie unreachable returns `502 {"error":"magpie unavailable", "detail": ...}`; the Magpie tab shows an inline error banner while the Proxy tab stays unaffected
- Restored this changelog (it was dropped in the v1.3.0 release)

## [1.3.0] - 2026-09-09

### Added
- Control UI reverse proxy — transparent HTTP + WebSocket reverse proxy for the OpenClaw web console (`/app/*`), stripping forwarding headers so OpenClaw's loopback attribution check passes (`5bd21e6`)
- One-click SSO console entry — `POST /api/ui/login` issues a signed HttpOnly admin-session cookie and returns an entry URL with the gateway token embedded in the URL fragment (`5bd21e6`)

### Changed
- `/app/*` routes (HTTP + WebSocket) now require the proxy's admin token via session cookie or Bearer header (`5bd21e6`)

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
