# Security Policy

## Reporting a vulnerability

Report privately via
[GitHub Security Advisories](https://github.com/ChuckBuilds/TampaWater-HA-AddOn/security/advisories/new)
rather than a public issue. Include what you observed, how to reproduce it, and
what an attacker could reach.

Personal project, maintained in spare time — expect a first response within about
a week.

## What this add-on handles

- **Your City of Tampa portal credentials**, stored by the Home Assistant
  Supervisor in `/data/options.json` and passed to the process as environment
  variables. Never logged, never written to the archive.
- **Your billing archive** — service periods, gallons, cost, meter reads — in
  `/data/cache/` inside the add-on container, and included in HA backups. Treat
  those backups as sensitive.
- It logs in to the portal as you over HTTPS. No browser is involved.

## Exposure model

- The dashboard is served over **Home Assistant ingress**, which HA
  authenticates. Nothing extra is needed for normal use.
- **The API port is not published by default.** If you publish it in the add-on's
  Network tab, `/data` and `/export` become reachable from your LAN — **set
  `auth_token` when you do**. The add-on warns at startup if no token is set.
- A request skips the token only when it genuinely arrives from the Supervisor
  network (`172.30.32.0/23`). The `X-Ingress-Path` header alone is **not**
  accepted as proof of ingress — headers are attacker-controlled. See
  `tests/test_auth.py`, which CI runs to keep that from regressing.

## Hardening in place

| | |
|---|---|
| Ingress exemption | Bound to the Supervisor source address, not a header |
| Token comparison | `secrets.compare_digest` (constant time) |
| API port | Unpublished by default; opt-in per install |
| Interactive API docs | Disabled (`/docs`, `/redoc`, `/openapi.json`) |
| CI actions | Pinned to commit SHAs, not movable tags |
| Workflow token | `permissions: contents: read` |
| Dependencies | Bounded ranges, tracked by Dependabot |
| Personal data | Pre-commit + CI scan blocks account numbers, addresses, meter serials, emails |
| Attack surface | No browser engine — plain HTTPS requests |

## Known limitations

- Sensor states are pushed over the Supervisor API using the token the Supervisor
  injects. Any add-on with `homeassistant_api: true` has comparable access.
- The bill archive is not encrypted at rest; it is protected by the same boundary
  as the rest of your Home Assistant data.
