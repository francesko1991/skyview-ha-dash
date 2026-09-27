"""On-demand, read-only Home Assistant health check.

Run through run-health.ps1 (credential loaded into process env vars only). Sends only the read commands in
HEALTH_WS / SUPERVISOR_GET / REST_GET below; never calls services, never writes. Produces a sanitized report
(no tokens, URLs, addresses, IDs, log message text or presence data) in maintenance/reports/.

  run-health.ps1                  full report (markdown + json) and a terminal summary
  run-health.ps1 --only backups   one area: system, backups, updates, integrations, devices, logs, dashboards
  run-health.ps1 --no-save        print only, write nothing
  run-health.ps1 --compare        also compare with the previous full report (new / resolved / top action)
"""
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ha_inventory as hi  # noqa: E402  (connection + scrub of token/url/host)

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "maintenance" / "reports"

# Everything this tool may send. Anything else is refused locally.
HEALTH_WS = {"auth/current_user", "get_config", "get_states", "config/entity_registry/list", "config/device_registry/list",
             "repairs/list_issues", "config_entries/get", "backup/info", "backup/config/info", "system_log/list",
             "recorder/info", "lovelace/dashboards/list", "lovelace/config", "lovelace/resources", "frontend/get_themes",
             "hacs/repositories/list", "supervisor/api"}
SUPERVISOR_GET = {"/core/info", "/supervisor/info", "/os/info", "/host/info", "/resolution/info", "/addons", "/core/stats"}
ADMIN_ONLY = {"repairs/list_issues", "config_entries/get", "backup/info", "backup/config/info", "system_log/list",
              "recorder/info", "hacs/repositories/list", "supervisor/api"}

OK, WATCH, ACTION, SKIP = "OK", "WATCH", "ACTION", "SKIPPED"
ICON = {OK: "✅", WATCH: "🟡", ACTION: "🔴", SKIP: "⚪"}

_REDACT = [
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
    (re.compile(r"\b(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}\b"), "<mac>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "<email>"),
    (re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://\S+"), "<url>"),  # any scheme: http(s), rtsp(s), ws(s)
    (re.compile(r"\?\S*token=\S+"), "?<query>"),
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<id>"),
    (re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b"), "<vin>"),          # vehicle identification numbers
    (re.compile(r"\b[0-9a-fA-F]{16,}\b"), "<id>"),
]


def clean(text):
    text = hi.scrub(text)
    for rx, repl in _REDACT:
        text = rx.sub(repl, text)
    return text


class Reader:
    """WebSocket reader limited to HEALTH_WS; supervisor calls limited to GET on SUPERVISOR_GET."""

    def __init__(self):
        self.ws = hi.HAWebSocket()
        self.admin = bool(self._send("auth/current_user")[1].get("is_admin"))

    def _send(self, cmd, **params):
        if cmd not in HEALTH_WS:
            raise RuntimeError(f"Refusing non-read command: {cmd}")
        if cmd == "supervisor/api" and (params.get("method") != "get" or params.get("endpoint") not in SUPERVISOR_GET):
            raise RuntimeError(f"Refusing supervisor call: {params}")
        ws = self.ws
        i = ws.next_id
        ws.next_id += 1
        ws.ws.send(json.dumps({"id": i, "type": cmd, **params}))
        while True:
            m = json.loads(ws.ws.recv())
            if m.get("id") == i and m.get("type") == "result":
                return m.get("success"), (m.get("result") if m.get("success") else (m.get("error") or {}).get("code"))

    def get(self, cmd, **params):
        if cmd in ADMIN_ONLY and not self.admin:
            return None
        ok, res = self._send(cmd, **params)
        return res if ok else None

    def sup(self, path):
        return self.get("supervisor/api", endpoint=path, method="get")

    def close(self):
        self.ws.close()


class Report:
    def __init__(self):
        self.checks, self.fixes, self.not_checked = [], [], []

    def add(self, area, name, status, detail):
        self.checks.append({"area": area, "check": name, "status": status, "detail": clean(detail)})

    def propose(self, fid, area, title, evidence, how, risk):
        self.fixes.append({"id": fid, "area": area, "title": title, "evidence": clean(evidence), "how": how, "risk": risk,
                           "status": "proposed — not applied"})

    def skip(self, area, name, why):
        self.add(area, name, SKIP, why)


def _age_hours(iso):
    if not iso:
        return None
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds() / 3600


def check_system(r, rep):
    cfg = r.get("get_config") or {}
    rep.add("system", "Core running", OK if cfg.get("state") == "RUNNING" and not cfg.get("safe_mode") and not cfg.get("recovery_mode")
            else ACTION, f"Home Assistant {cfg.get('version')}, state {cfg.get('state')}, safe mode {cfg.get('safe_mode')}, "
            f"recovery mode {cfg.get('recovery_mode')}")
    if not r.admin:
        for n in ("Repairs", "Supervisor health", "Disk", "Recorder"):
            rep.skip("system", n, "needs an admin token")
        return
    issues = [i for i in (r.get("repairs/list_issues") or {}).get("issues", []) if not i.get("dismissed_version")]
    sev = Counter(i.get("severity") for i in issues)
    rep.add("system", "Repairs", ACTION if sev.get("critical") or sev.get("error") else (WATCH if issues else OK),
            f"{len(issues)} open repair(s)" + (": " + ", ".join(f"{i.get('domain')} ({i.get('severity')})" for i in issues) if issues else ""))
    res = r.sup("/resolution/info") or {}
    bad = res.get("unhealthy", []) + res.get("unsupported", [])
    rissues = [i.get("type") for i in res.get("issues", [])]
    cosmetic = rissues == ["no_current_backup"] and backup_state(r)["complete"]  # explained under Backups
    rep.add("system", "Supervisor health", ACTION if bad else (WATCH if rissues and not cosmetic else OK),
            f"unhealthy/unsupported: {bad or 'none'}; issues: {rissues or 'none'}; suggestions: "
            f"{[s.get('type') for s in res.get('suggestions', [])] or 'none'}")
    host = r.sup("/host/info") or {}
    total, free, life = host.get("disk_total") or 0, host.get("disk_free") or 0, host.get("disk_life_time")
    pct = free / total * 100 if total else 0
    rep.add("system", "Disk", ACTION if pct < 10 or free < 5 else (WATCH if pct < 20 or (life or 0) >= 70 else OK),
            f"{free:.0f} GB free of {total:.0f} GB ({pct:.0f}%); disk lifetime used: {life if life is not None else 'n/a'}%")
    stats = r.sup("/core/stats") or {}
    rep.add("system", "Core load", WATCH if (stats.get("memory_percent") or 0) > 70 else OK,
            f"CPU {stats.get('cpu_percent')}%, memory {stats.get('memory_percent')}%")
    rec = r.get("recorder/info") or {}
    rep.add("system", "Recorder (history database)",
            ACTION if not rec.get("recording") or not rec.get("thread_running") or rec.get("migration_in_progress")
            else (WATCH if (rec.get("backlog") or 0) > 100 else OK),
            f"recording {rec.get('recording')}, backlog {rec.get('backlog')}, migration {rec.get('migration_in_progress')}")


ALL_FOLDERS = {"share", "addons/local", "ssl", "media"}
PC_COPIES = ROOT / "ha-backups"  # filled on demand by scripts/run-backup-copy.ps1


def backup_state(r):
    """Newest backup and whether it is complete (recent, no failures, config + database + every add-on + all folders)."""
    if hasattr(r, "_bk"):
        return r._bk
    info, conf = r.get("backup/info") or {}, (r.get("backup/config/info") or {}).get("config", {})
    bks = sorted(info.get("backups", []), key=lambda b: b.get("date") or "")
    newest = bks[-1] if bks else {}
    cb = conf.get("create_backup") or {}
    installed = len((r.sup("/addons") or {}).get("addons", []))
    gaps = []
    if not newest:
        gaps.append("no backup")
    else:
        if (_age_hours(newest.get("date")) or 999) > 26:
            gaps.append("older than 26 h")
        if newest.get("failed_agent_ids"):
            gaps.append("a location failed")
        if not newest.get("homeassistant_included") or not newest.get("database_included"):
            gaps.append("settings or database missing")
        if len(newest.get("addons") or []) < installed:
            gaps.append(f"{len(newest.get('addons') or [])} of {installed} add-ons")
        if not ALL_FOLDERS <= set(cb.get("include_folders") or []):
            gaps.append("not all folders selected")
    r._bk = {"info": info, "conf": conf, "bks": bks, "newest": newest, "complete": not gaps, "gaps": gaps}
    return r._bk


def check_backups(r, rep):
    if not r.admin:
        rep.skip("backups", "Backups", "needs an admin token")
        return
    st = backup_state(r)
    info, conf, bks, newest = st["info"], st["conf"], st["bks"], st["newest"]
    last_ok = info.get("last_completed_automatic_backup") or conf.get("last_completed_automatic_backup")
    last_try = conf.get("last_attempted_automatic_backup")
    age = _age_hours(last_ok)
    failed = bool(last_try and last_ok and last_try > last_ok)
    rep.add("backups", "Latest automatic backup",
            ACTION if age is None or age > 7 * 24 or failed else (WATCH if age > 26 else OK),
            (f"last success {age:.0f} h ago" if age is not None else "no automatic backup recorded")
            + (" — the most recent attempt FAILED" if failed else ""))
    sched = conf.get("schedule") or {}
    rep.add("backups", "Schedule & retention", OK if sched.get("recurrence") not in (None, "never") else ACTION,
            f"{sched.get('recurrence')} backups, keep {(conf.get('retention') or {}).get('copies')} copies; {len(bks)} stored")
    if newest:
        enc = all((a or {}).get("protected") for a in (newest.get("agents") or {}).values())
        rep.add("backups", "Newest backup complete", OK if st["complete"] else WATCH,
                ("complete: settings, database, every add-on, all folders, no failed location" if st["complete"]
                 else "gaps: " + ", ".join(st["gaps"])) + f"; encrypted {enc}")

    # Off-device copy: a second Home Assistant location (e.g. Google Drive), or the on-demand copy on this PC.
    local_agents = {"hassio.local", "backup.local"}
    loc = (newest.get("agents") or {}) if newest else {}
    ref = next((v.get("size") for k, v in loc.items() if k in local_agents and v), None)
    remote = [k for k, v in loc.items() if k not in local_agents and v and k not in (newest.get("failed_agent_ids") or [])
              and (not ref or abs((v.get("size") or 0) - ref) <= ref * 0.01)]
    sizes = {b["backup_id"]: next((v.get("size") for k, v in (b.get("agents") or {}).items() if k in local_agents and v), None)
             for b in bks}
    pc = sorted(PC_COPIES.glob("ha-backup-*.tar"), key=lambda p: p.name, reverse=True) if PC_COPIES.exists() else []
    pc_ok = [p for p in pc if any(p.name.endswith(f"-{bid}.tar") and sz and p.stat().st_size == sz for bid, sz in sizes.items())]
    if remote:
        rep.add("backups", "Off-device copy", OK, f"newest backup is also on {', '.join(remote)} (size matches, no failure)")
    elif pc_ok:
        newest_pc = pc_ok[0]
        on_newest = newest and newest_pc.name.endswith(f"-{newest.get('backup_id')}.tar")
        pc_date = datetime.strptime(newest_pc.name[10:23], "%Y%m%d-%H%M")
        days = (datetime.now() - pc_date).total_seconds() / 86400
        rep.add("backups", "Off-device copy", OK if on_newest or days <= 7 else WATCH,
                f"{len(pc_ok)} verified cop{'y' if len(pc_ok) == 1 else 'ies'} on this PC (sizes match); newest copy "
                + ("is the newest backup" if on_newest else f"is {days:.0f} day(s) behind — run scripts\\run-backup-copy.ps1 --apply")
                + "; not off-site (same building)")
    else:
        rep.add("backups", "Off-device copy", WATCH, "every copy is on the Home Assistant disk itself")
        rep.propose("B1", "backups", "Keep a backup copy off the Home Assistant machine",
                    "no second location and no verified copy on this PC",
                    "Run scripts\\run-backup-copy.ps1 (dry run), then --apply; or add a cloud or network location in "
                    "Settings → System → Backups → Locations.",
                    "Low. No change to existing backups; a disk failure today would lose every copy.")

    res = r.sup("/resolution/info") or {}
    if any(i.get("type") == "no_current_backup" for i in res.get("issues", [])):
        if st["complete"]:
            rep.add("backups", "Supervisor backup check", OK,
                    "Supervisor's 'no current backup' is cosmetic: Home Assistant makes backups through Supervisor's "
                    "partial-backup call, and the newest backup has everything")
        else:
            rep.add("backups", "Supervisor backup check", WATCH, "Supervisor reports 'no current backup' and the newest "
                    "backup has gaps: " + ", ".join(st["gaps"]))
            rep.propose("B2", "backups", "Close the gaps in the automatic backup",
                        "gaps: " + ", ".join(st["gaps"]),
                        "Settings → System → Backups → Backup settings: include the database, all add-ons and all folders; "
                        "check the last backup finished without errors.", "Low. Settings only; no data removed.")


def check_updates(r, rep, states):
    upd = [s for s in states if s["entity_id"].startswith("update.")]
    pending = [s for s in upd if s["state"] == "on"]
    rep.add("updates", "Pending updates", WATCH if pending else OK,
            f"{len(pending)} of {len(upd)}" + "".join(
                f"; {s['attributes'].get('friendly_name')} {s['attributes'].get('installed_version')} → "
                f"{s['attributes'].get('latest_version')}" for s in pending))
    un = [s for s in upd if s["state"] == "unavailable"]
    if un:
        rep.add("updates", "Update checks unavailable", WATCH, f"{len(un)} update entities can't check for updates")
    if pending:
        rep.propose("U1", "updates", "Install pending updates one at a time",
                    "; ".join(s["attributes"].get("friendly_name", "") for s in pending),
                    "Settings → System → Updates, after a fresh backup; one device at a time, check it comes back online.",
                    "Medium for firmware (device reboots; rarely fails). Never update all cameras at once.")
    if r.admin:
        hacs = r.get("hacs/repositories/list")
        if hacs is not None:
            p = [x.get("name") for x in hacs if x.get("pending_upgrade")]
            rep.add("updates", "HACS custom components", WATCH if p else OK, f"{len(p)} pending" + (f": {', '.join(p)}" if p else ""))
        addons = (r.sup("/addons") or {}).get("addons", [])
        au = [a.get("name") for a in addons if a.get("update_available")]
        rep.add("updates", "Add-ons", WATCH if au else OK, f"{len(addons)} installed, {len(au)} with updates"
                + (f": {', '.join(au)}" if au else "") + f"; not running: {[a.get('name') for a in addons if a.get('state') != 'started'] or 'none'}")
        for path, label in (("/core/info", "Core"), ("/supervisor/info", "Supervisor"), ("/os/info", "Operating system")):
            i = r.sup(path) or {}
            rep.add("updates", label, WATCH if i.get("update_available") else OK, f"{i.get('version')} (latest {i.get('version_latest')})")


def check_integrations(r, rep):
    if not r.admin:
        rep.skip("integrations", "Integration status", "needs an admin token")
        return
    entries = [e for e in (r.get("config_entries/get") or []) if e.get("source") != "ignore"]  # ignored discoveries
    bad = [e for e in entries if e["state"] != "loaded" and not e.get("disabled_by")]
    hard = [e for e in bad if e["state"] in ("setup_error", "migration_error", "failed_unload")]
    groups = Counter((e["domain"], e["state"]) for e in bad)
    rep.add("integrations", "Integration status", ACTION if hard else (WATCH if bad else OK),
            f"{len(entries)} entries; not loaded: " + (", ".join(f"{d} ({s}{', ×' + str(c) if c > 1 else ''})"
                                                                  for (d, s), c in groups.items()) or "none")
            + f"; disabled: {sum(1 for e in entries if e.get('disabled_by'))}")
    for n, ((d, s), c) in enumerate(groups.items(), 1):
        rep.propose(f"I{n}", "integrations", f"Resolve {d} ({s}{', ' + str(c) + ' entries' if c > 1 else ''})", f"config entry state {s}",
                    "Settings → Devices & services → the entry: check its error, then reload, reconfigure, or delete if no longer used.",
                    "Low to medium. Deleting an entry removes its entities; decide case by case.")


def check_devices(r, rep, states):
    reg = {e["entity_id"]: e for e in (r.get("config/entity_registry/list") or [])}
    skip_domains = {"person", "device_tracker", "zone"}  # presence: never reported
    un = [s for s in states if s["state"] == "unavailable" and s["entity_id"].split(".")[0] not in skip_domains
          and not reg.get(s["entity_id"], {}).get("disabled_by")]  # disabled on purpose (placeholders until restart)
    by = Counter(reg.get(s["entity_id"], {}).get("platform") or "unregistered" for s in un)
    rep.add("devices", "Unavailable entities", WATCH if un else OK,
            f"{len(un)} of {len(states)}; by integration: " + (", ".join(f"{k} {v}" for k, v in by.most_common()) or "none"))
    devs = r.get("config/device_registry/list") or []
    ents_by_dev = {}
    for e in reg.values():
        if e.get("device_id") and not e.get("disabled_by"):
            ents_by_dev.setdefault(e["device_id"], []).append(e["entity_id"])
    st = {s["entity_id"]: s["state"] for s in states}
    dead = [d for d in devs if ents_by_dev.get(d["id"]) and all(st.get(x) in ("unavailable", None) for x in ents_by_dev[d["id"]])]
    rep.add("devices", "Devices fully offline", WATCH if dead else OK,
            f"{len(dead)} device(s)" + (": " + ", ".join(sorted((d.get("name_by_user") or d.get("name") or "?") for d in dead)[:12]) if dead else ""))
    batt = [s for s in states if s["attributes"].get("device_class") == "battery" and s["attributes"].get("unit_of_measurement") == "%"]
    low = [(s["attributes"].get("friendly_name"), float(s["state"])) for s in batt if re.fullmatch(r"\d+(\.\d+)?", s["state"]) and float(s["state"]) < 20]
    rep.add("devices", "Batteries", ACTION if any(v < 10 for _, v in low) else (WATCH if low else OK),
            f"{len(batt)} battery sensors; below 20%: " + (", ".join(f"{n} {v:.0f}%" for n, v in low) or "none"))
    if dead:
        rep.propose("D1", "devices", "Review devices that are completely offline",
                    f"{len(dead)} device(s) with every entity unavailable",
                    "Power-cycle or re-pair each; remove ones you no longer own (Settings → Devices).",
                    "Low. Removing a device deletes its entities and any dashboard references to them.")


def check_logs(r, rep):
    if not r.admin:
        rep.skip("logs", "Error log", "needs an admin token")
        return
    logs = r.get("system_log/list") or []
    lv = Counter(x.get("level") for x in logs)
    src = Counter()
    for x in logs:
        if x.get("level") in ("ERROR", "CRITICAL"):
            src[clean(x.get("name", "?"))] += x.get("count", 1)
    rep.add("logs", "Error log (since last restart)", WATCH if lv.get("ERROR") or lv.get("CRITICAL") else OK,
            f"{lv.get('ERROR', 0)} error and {lv.get('WARNING', 0)} warning types; top error sources: "
            + (", ".join(f"{k} ×{v}" for k, v in src.most_common(5)) or "none"))


def check_dashboards(r, rep, states):
    known = {s["entity_id"] for s in states}
    st = {s["entity_id"]: s["state"] for s in states}
    dash = r.get("lovelace/dashboards/list") or []
    rx = re.compile(r"\b(?:alarm_control_panel|automation|binary_sensor|button|camera|climate|cover|fan|image|input_\w+|light|lock|"
                    r"media_player|number|remote|scene|script|select|sensor|switch|timer|update|vacuum|weather)\.[a-z0-9_]+\b")
    for d in dash:
        if d.get("mode") != "storage":
            continue
        cfg = r.get("lovelace/config", url_path=d["url_path"], force=False)
        if not cfg:
            continue
        refs = set()

        def walk(o, key=None):  # action names (light.toggle) look like entity IDs; skip those values
            if isinstance(o, dict):
                for k, v in o.items():
                    walk(v, k)
            elif isinstance(o, list):
                for v in o:
                    walk(v, key)
            elif isinstance(o, str) and key not in ("perform_action", "service", "action"):
                refs.update(rx.findall(o))
        walk(cfg)
        missing = sorted(e for e in refs if e not in known)
        unavail = sorted(e for e in refs if st.get(e) == "unavailable")
        rep.add("dashboards", f"Dashboard '{d.get('title')}'", ACTION if missing else (WATCH if unavail else OK),
                f"{len(refs)} entities referenced; missing {len(missing)}" + (f" ({', '.join(missing[:8])})" if missing else "")
                + f"; unavailable {len(unavail)}")
        if missing:
            rep.propose("L1", "dashboards", f"Fix {len(missing)} missing entity reference(s) on '{d.get('title')}'",
                        ", ".join(missing[:8]), "Dry run with scripts/run-apply.ps1, then approve.", "Low. Dashboard only.")
    themes = (r.get("frontend/get_themes") or {}).get("themes", {})
    rep.add("dashboards", "Theme 'Home Panel' loaded", OK if "Home Panel" in themes else ACTION, f"{len(themes)} themes loaded")
    res = r.get("lovelace/resources")
    if res is not None:
        rep.add("dashboards", "Custom card resources", OK if res else WATCH, f"{len(res)} registered")


NOT_CHECKED = [  # things the API can't answer; see maintenance/SSH_CHECKS.md
    ("Database file size and integrity", "file on disk; the API only reports that recording works"),
    ("Configuration files (YAML, secrets.yaml, custom_components not managed by HACS)", "files on disk"),
    ("Backup archive contents and a test restore", "the API lists backups but can't open or verify an archive"),
    ("Disk health beyond the lifetime percentage (SMART, I/O errors)", "host level; limited even over SSH on Home Assistant OS"),
]

AREAS = {"system": check_system, "backups": check_backups, "updates": check_updates, "integrations": check_integrations,
         "devices": check_devices, "logs": check_logs, "dashboards": check_dashboards}
NEEDS_STATES = {"updates", "devices", "dashboards"}


def render(rep, admin, stamp):
    worst = ACTION if any(c["status"] == ACTION for c in rep.checks) else (WATCH if any(c["status"] == WATCH for c in rep.checks) else OK)
    n = Counter(c["status"] for c in rep.checks)
    out = [f"# Home Assistant health report — {stamp}", "",
           f"**Overall: {ICON[worst]} {worst}** · {n[OK]} OK · {n[WATCH]} watch · {n[ACTION]} action · {n[SKIP]} skipped"
           + ("" if admin else " · ⚠ limited mode (token is not admin)"), "",
           "Read-only check. Nothing in Home Assistant was changed. Sensitive values are redacted.", "",
           "## 1. Observations", "", "| | Area | Check | Detail |", "|---|---|---|---|"]
    order = {ACTION: 0, WATCH: 1, OK: 2, SKIP: 3}
    for c in sorted(rep.checks, key=lambda c: (order[c["status"]], c["area"])):
        out.append(f"| {ICON[c['status']]} | {c['area']} | {c['check']} | {c['detail'].replace('|', '/')} |")
    out += ["", "## 2. Proposed fixes (not applied)", ""]
    if rep.fixes:
        for f in rep.fixes:
            out += [f"### {f['id']} · {f['title']}", f"- **Evidence:** {f['evidence']}", f"- **How:** {f['how']}",
                    f"- **Risk:** {f['risk']}", f"- **Status:** {f['status']}", ""]
    else:
        out += ["None.", ""]
    out += ["## 3. Approved changes", "",
            "None from this report. Approved changes are recorded in `maintenance/CHANGES.md` with the approval, the backup "
            "taken first and the verification result.", "",
            "## Not checked (API can't see these)", ""]
    out += [f"- {a} — {why}" for a, why in NOT_CHECKED]
    return "\n".join(out) + "\n", worst


AREA_PRIORITY = ["backups", "system", "integrations", "updates", "devices", "logs", "dashboards"]
RANK = {OK: 0, SKIP: 1, WATCH: 2, ACTION: 3}


def previous_report(before_name):
    """Newest full report (not an --only report) older than the one being written."""
    full = sorted(p for p in REPORTS.glob("health-*.json") if re.fullmatch(r"health-\d{8}-\d{4}\.json", p.name) and p.name < before_name)
    return (full[-1], json.loads(full[-1].read_text(encoding="utf-8"))) if full else (None, None)


def compare(prev, rep):
    """New issues (worse or newly flagged), resolved issues (now OK or gone), still open, and one top action."""
    old = {(c["area"], c["check"]): c for c in (prev or {}).get("checks", [])}
    new = {(c["area"], c["check"]): c for c in rep.checks}
    issue = lambda c: c and c["status"] in (WATCH, ACTION)  # noqa: E731
    added = [c for k, c in new.items() if issue(c) and (k not in old or RANK[c["status"]] > RANK[old[k]["status"]])]
    resolved = [old[k] for k in old if issue(old[k]) and not issue(new.get(k))]
    still = [c for k, c in new.items() if issue(c) and c not in added]
    skipped = [c for c in rep.checks if c["status"] == SKIP]
    pool = sorted([c for c in rep.checks if issue(c)],
                  key=lambda c: (-RANK[c["status"]], c not in added, AREA_PRIORITY.index(c["area"]) if c["area"] in AREA_PRIORITY else 99))
    top = None
    if pool:
        c = pool[0]
        fix = next((f for f in rep.fixes if f["area"] == c["area"]), None)
        top = f"{c['area']} · {c['check']}: " + (f"{fix['title']} ({fix['id']}). {fix['how']}" if fix else c["detail"])
    return {"added": added, "resolved": resolved, "still": still, "skipped": skipped, "top": top}


def render_diff(d, prev_name):
    line = lambda c: f"- {ICON[c['status']]} **{c['area']} · {c['check']}**: {c['detail']}"  # noqa: E731
    out = [f"## Changes since the previous report ({prev_name or 'none: this is the first full report'})", ""]
    out += ["**New issues**", ""] + ([line(c) for c in d["added"]] or ["- None"]) + [""]
    out += ["**Resolved**", ""] + ([f"- ✅ **{c['area']} · {c['check']}** (was {c['status']})" for c in d["resolved"]] or ["- None"]) + [""]
    out += [f"**Still open:** {len(d['still'])}" + (" · " + ", ".join(f"{c['area']} · {c['check']}" for c in d["still"]) if d["still"] else ""), ""]
    if d["skipped"]:
        out += ["**Skipped (limited access, not elevated):** " + ", ".join(f"{c['area']} · {c['check']}" for c in d["skipped"]), ""]
    out += ["**Top recommended action:** " + (d["top"] or "None, everything is OK."), ""]
    return "\n".join(out)


def main():
    args = sys.argv[1:]
    only = args[args.index("--only") + 1] if "--only" in args else None
    if only and only not in AREAS:
        sys.exit(f"--only must be one of: {', '.join(AREAS)}")
    r = Reader()
    rep = Report()
    states = r.get("get_states") or []
    for area, fn in AREAS.items():
        if only and area != only:
            continue
        fn(r, rep, states) if area in NEEDS_STATES else fn(r, rep)
    r.close()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    md, worst = render(rep, r.admin, stamp)
    name = f"health-{datetime.now():%Y%m%d-%H%M}{'-' + only if only else ''}"
    diff = None
    if "--compare" in args and not only:
        prev_path, prev = previous_report(name + ".json")
        diff = compare(prev, rep)
        head, rest = md.split("## 1. Observations", 1)
        md = head + render_diff(diff, prev_path.stem if prev_path else None) + "\n## 1. Observations" + rest
    for c in rep.checks:
        print(f"{ICON[c['status']]} {c['status']:7} {c['area']:12} {c['check']}: {c['detail'][:110]}")
    print(f"\nOverall: {worst} · {len(rep.fixes)} proposed fix(es)" + ("" if r.admin else " · limited mode (not admin)"))
    if diff is not None:
        print(f"New issues: {len(diff['added'])}" + "".join(f"\n  + {c['area']} · {c['check']}: {c['detail'][:100]}" for c in diff["added"]))
        print(f"Resolved: {len(diff['resolved'])}" + "".join(f"\n  - {c['area']} · {c['check']}" for c in diff["resolved"]))
        print(f"Skipped (limited access): {', '.join(c['area'] + ' · ' + c['check'] for c in diff['skipped']) or 'none'}")
        print(f"Top recommended action: {diff['top'] or 'none'}")
    if "--no-save" not in args:
        REPORTS.mkdir(parents=True, exist_ok=True)
        base = REPORTS / name
        base.with_suffix(".md").write_text(md, encoding="utf-8")
        data = {"generated": stamp, "admin": r.admin, "overall": worst, "checks": rep.checks, "proposed_fixes": rep.fixes}
        if diff is not None:
            data["changes"] = {k: v for k, v in diff.items()}
        base.with_suffix(".json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Report: {base.with_suffix('.md').relative_to(ROOT)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        sys.exit(f"ERROR: {type(e).__name__}: {clean(e)}")
