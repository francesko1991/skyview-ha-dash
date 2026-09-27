# Skyview HA Dash

A wall-tablet and phone dashboard for [Home Assistant](https://www.home-assistant.io/), with a custom
"Twilight sky" / "Morning sky" theme and a small set of Python tools for working on a live Home Assistant
setup safely.

Open [`prototype/home-panel.html`](prototype/home-panel.html) in a browser for a standalone demo with
sample values (no Home Assistant needed).

> **Heads-up:** this is my own home's configuration, shared as a reference. Entity IDs, rooms and devices
> are specific to my setup, so expect to swap them for your own rather than paste and go.

## The dashboard

- **Sections layout** built for phones and tablets: a header with a clock, three shortcut pills, live
  alert chips and a "what to wear" card, then swipeable card pairs (Security | Climate, Garage | Lights,
  Cameras, Cleaning | Media).
- **Subviews** for climate, lights, media, cleaning and per-room pages, linked from the home screen.
- **Safe controls:** locks, sirens, alarm disarm and "all lights off" always ask for confirmation.
  Unavailable devices move into their own group instead of cluttering the main cards.
- **Theme** with light and dark variants that follow each device's own setting, plus hand-drawn SVG sky
  backgrounds (`ha-dashboard/backgrounds/make_backgrounds.py` regenerates them).
- **What to wear:** an optional header card that asks Home Assistant's AI Task for clothing advice based
  on today's forecast and, on work days, the commute time. Only weather numbers, times and drive minutes
  are sent; never names, addresses or locations. Setup: [`ha-dashboard/outfit/SETUP.md`](ha-dashboard/outfit/SETUP.md).

**Needs:** Home Assistant 2026.9 or newer, and [Mushroom](https://github.com/piitaya/lovelace-mushroom)
5.x (used only for the alert chips). The theme is optional.

Install steps: [`ha-dashboard/INSTALL.md`](ha-dashboard/INSTALL.md).

## The tooling

Everything talks to Home Assistant through a **non-admin, long-lived token** and is **read-only by
default**.

| Tool | What it does |
|---|---|
| `scripts/setup-ha-credentials.ps1` | One-time: stores the Home Assistant URL and token encrypted with Windows DPAPI, outside the project folder. |
| `scripts/run-ha.ps1 --check \| --inventory \| --pull <dashboard>` | Read-only: connectivity check, a sanitized device/entity inventory, or a pull of a live dashboard as JSON. |
| `ha-dashboard/validate_dashboard.py` | Offline checks: YAML and Jinja syntax, every entity reference, card compatibility with the installed frontend version, and column layout at phone and tablet widths. |
| `scripts/run-health.ps1` | Read-only health report: updates, backups, integrations, offline devices, logs. |
| `scripts/run-apply.ps1 <step>` | The only tool that changes Home Assistant. Each step is a dry run that prints exactly what it would send; it only applies with `--apply`, after taking a backup. |
| `scripts/run-backup-copy.ps1` | Copies the newest (still encrypted) Home Assistant backups to this PC. |

Maintenance workflow: [`maintenance/README.md`](maintenance/README.md).

**Needs:** Windows PowerShell (for the credential wrappers) and Python 3.13 with
`pip install requests websocket-client pyyaml jinja2`.

### Privacy by design

- The token never touches the project folder; wrappers load it into process-scoped environment variables
  and clear them on exit.
- WebSocket and REST calls are limited to explicit allowlists of read commands.
- Pulls and reports scrub URLs, IP and MAC addresses, tokens, e-mail addresses and location data before
  anything is saved, and a pull is refused if a dashboard contains any of them.
- Real data from the house (inventory, dashboard snapshots, health reports, backups) is kept local and
  excluded by [`.gitignore`](.gitignore). That's why the builder and validator expect an `inventory/`
  folder you generate yourself with `scripts/run-ha.ps1 --inventory`.

## Layout

```
ha-dashboard/   dashboard YAML, theme, backgrounds, validator, "what to wear" feature
prototype/      standalone HTML demo of the dashboard (sample values)
scripts/        read-only inventory/health tools and the approval-gated apply tool
maintenance/    maintenance workflow and optional SSH checks
```
