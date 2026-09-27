# Maintenance workflow

On-demand only: nothing runs on a schedule, and nothing is installed on Home Assistant. Every run is started by you (or by
Claude when you ask) from this project.

## The loop

1. **Observe:** `scripts\run-health.ps1` → read-only report in `reports\health-<date>.md`.
2. **Propose:** the report's *Proposed fixes* section (IDs like `B1`, `I2`). Nothing in it has been applied.
3. **Approve:** you say which fix IDs to do, in chat.
4. **Change:** each approved fix is done one at a time. It's either a UI step you do yourself, or a dry-run → yes → backup →
   apply step in `scripts\run-apply.ps1`, as with the dashboard work.
5. **Record:** one line per change in `CHANGES.md` (fix ID, approval, backup reference, how it was verified).
6. **Verify:** re-run the check for that area, for example `scripts\run-health.ps1 --only backups`.

Observations, proposals and changes stay separate: reports never change anything, and `CHANGES.md` only lists what you approved.

## Commands (all read-only)

| Command | What it does |
|---|---|
| `scripts\run-health.ps1` | Full report (markdown + json) in `maintenance\reports\` and a summary in the terminal |
| `scripts\run-health.ps1 --only <area>` | One area: `system`, `backups`, `updates`, `integrations`, `devices`, `logs`, `dashboards` |
| `scripts\run-health.ps1 --no-save` | Print only |
| `scripts\run-backup-copy.ps1` | Dry run: which backups would be copied to `ha-backups\` (keeps the 2 newest) |
| `scripts\run-backup-copy.ps1 --apply` | Downloads them (still encrypted), checks size and that each archive opens, and moves older copies to the Recycle Bin. The only command here that writes files; it never changes Home Assistant |
| `scripts\run-ha.ps1 --check` | Connectivity and whether the token is admin |
| `scripts\run-ha.ps1 --inventory` | Refresh the device and entity inventory |
| `ha-dashboard\validate_dashboard.py` | Deep dashboard validation (card options, templates, layout) after `run-ha.ps1 --pull home-panel` |

Suggested rhythm: a full report monthly, before and after any update, and whenever something looks wrong.

**Daily scheduled check (since 2026-09-27):** the Claude Code Desktop task `home-assistant-health` runs
`scripts\run-health.ps1 --compare` every day at 9:00 AM Eastern (the app adds about a minute), in this folder. It is read-only,
saves `reports\health-<date>.md/.json` with a "Changes since the previous report" section (new issues, resolved issues, still
open, skipped checks, top recommended action), and replies with a short summary. The project's saved approvals
(`.claude\settings.local.json`) are limited to the read-only health, inventory and dashboard-pull commands, so a run cannot apply
changes. View, run now or pause it in the app sidebar: **Scheduled → home-assistant-health**. It only runs while the app is
open; if the app was closed at 9 AM it runs on next launch. Uses the Claude Pro plan (no API key; extra usage is off).

## What the API covers and what needs SSH

| Area | Check | Via API | Needs admin token | Needs SSH |
|---|---|---|---|---|
| System | Core running, safe/recovery mode, version | ✅ | no | |
| | Repairs (open issues) | ✅ | yes | |
| | Supervisor health (unhealthy, unsupported, issues, suggestions) | ✅ | yes | |
| | Disk free, disk lifetime used, CPU and memory | ✅ | yes | |
| | Recorder running, backlog, migration | ✅ | yes | |
| | Database file size and integrity | | | ✅ |
| Backups | Last automatic backup, failures, schedule, retention | ✅ | yes | |
| | Locations (on-device only or off-device) and contents | ✅ | yes | |
| | Open an archive, or test a restore | | | ✅ (or a spare machine) |
| Updates | Core, Supervisor, OS, add-ons, device firmware, HACS | ✅ | partly (entities no, rest yes) | |
| Integrations | Entries not loaded, setup errors, retries | ✅ | yes | |
| | Why an integration fails (its log lines) | ✅ counts; text redacted | yes | full text: add-on log viewer or SSH |
| Devices | Unavailable entities, fully offline devices, low batteries | ✅ | no | |
| Logs | Error and warning counts and sources since restart | ✅ | yes | |
| Dashboards | Missing or unavailable entity references, theme, card resources | ✅ | no | |
| Config files | YAML files, `secrets.yaml`, custom components outside HACS | | | ✅ |
| Host | SMART disk data, I/O errors | | | ✅ (limited on HA OS) |

**Summary:** the routine report needs no SSH. SSH is only for the four file- and host-level checks in `SSH_CHECKS.md`, which you
run yourself in the Advanced SSH & Web Terminal add-on when needed.

## Access

- **Credential:** the existing DPAPI-encrypted token in `%USERPROFILE%\.ha-dashboard\` (outside this project). No new
  credentials. Nothing is written to the repository or to reports: the scripts load it into process-scoped environment variables and clear them on exit.
- **Admin:** most health checks need the token's user (`dashboard-reader`) to be an Administrator. Recommended: switch
  Administrator on just before a check and off afterwards. Without admin, the tool runs in **limited mode**: system state,
  updates from entities, devices, batteries and dashboards still work; the rest is marked *Skipped*.
- **SSH:** not used by Claude. No keys created or stored.

## Privacy in reports

Reports contain counts, versions, integration and device names, and statuses. Before anything is written, the tool redacts
IP and MAC addresses, URLs, e-mail addresses, IDs and vehicle numbers. It never includes log message text, presence
(`person` and `device_tracker`) or locations.

## Files

```
maintenance/
  README.md        this plan
  SSH_CHECKS.md    optional file- and host-level checks you run yourself
  CHANGES.md       approved changes only
  reports/         health-<date>.md / .json (sanitized)
scripts/
  ha_health.py     read-only checks (own allowlist of read commands)
  run-health.ps1   loads the credential for one run
```
