"""Read-only Home Assistant inventory for dashboard design.

Reads HA_URL / HA_TOKEN from the environment (set by run-ha.ps1). Uses only read
commands: REST GET /api/, and WebSocket auth/current_user, get_config, *_registry/list,
get_states, get_services. Never calls services, never fetches camera images/streams,
never writes to Home Assistant.

Usage:
  run-ha.ps1 --check        connectivity check only
  run-ha.ps1 --inventory    write sanitized inventory to ./inventory/
"""
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
import websocket

URL = os.environ.get("HA_URL", "").rstrip("/")
TOKEN = os.environ.get("HA_TOKEN", "")
OUT_DIR = Path(__file__).resolve().parent.parent / "inventory"

# Only these commands may be sent. Anything else is refused locally.
ALLOWED_WS = {
    "auth/current_user", "get_config", "get_states", "get_services",
    "config/area_registry/list", "config/floor_registry/list",
    "config/device_registry/list", "config/entity_registry/list",
    # dashboard inspection (read-only)
    "lovelace/dashboards/list", "lovelace/config", "lovelace/resources", "frontend/get_themes",
}

# Domains omitted entirely: zones are named places (location data).
EXCLUDED_DOMAINS = {"zone"}
# Domains whose state reveals presence; we only ever record availability anyway.
PRESENCE_DOMAINS = {"person", "device_tracker"}
READ_ONLY_DOMAINS = {
    "sensor", "binary_sensor", "person", "device_tracker", "sun", "weather",
    "event", "image", "calendar", "tts", "stt", "conversation", "wake_word",
    "todo", "geo_location", "air_quality", "assist_satellite",
}

# Capability attributes kept (whitelist). Everything else is dropped, which
# excludes entity_picture/access_token (cameras), coordinates, IPs, URLs, etc.
CAPABILITY_ATTRS = [
    "device_class", "unit_of_measurement", "state_class",
    "supported_color_modes", "min_color_temp_kelvin", "max_color_temp_kelvin", "effect_list",
    "hvac_modes", "fan_modes", "preset_modes", "swing_modes", "swing_horizontal_modes",
    "min_temp", "max_temp", "target_temp_step", "min_humidity", "max_humidity",
    "available_modes", "operation_list", "options", "min", "max", "step", "mode",
    "source_list", "sound_mode_list", "fan_speed_list", "available_tones",
    "frontend_stream_type", "event_types",
]

FEATURES = {
    "light": {4: "effect", 8: "flash", 32: "transition"},
    "cover": {1: "open", 2: "close", 4: "set_position", 8: "stop", 16: "open_tilt",
              32: "close_tilt", 64: "stop_tilt", 128: "set_tilt_position"},
    "valve": {1: "open", 2: "close", 4: "set_position", 8: "stop"},
    "climate": {1: "target_temperature", 2: "target_temperature_range", 4: "target_humidity",
                8: "fan_mode", 16: "preset_mode", 32: "swing_mode", 64: "aux_heat",
                128: "turn_off", 256: "turn_on", 512: "swing_horizontal_mode"},
    "fan": {1: "set_speed", 2: "oscillate", 4: "direction", 8: "preset_mode",
            16: "turn_off", 32: "turn_on"},
    "media_player": {1: "pause", 2: "seek", 4: "volume_set", 8: "volume_mute",
                     16: "previous_track", 32: "next_track", 128: "turn_on", 256: "turn_off",
                     512: "play_media", 1024: "volume_step", 2048: "select_source", 4096: "stop",
                     8192: "clear_playlist", 16384: "play", 32768: "shuffle_set",
                     65536: "select_sound_mode", 131072: "browse_media", 262144: "repeat_set",
                     524288: "grouping", 1048576: "announce", 2097152: "enqueue",
                     4194304: "search_media"},
    "lock": {1: "open"},
    "vacuum": {2: "turn_off", 4: "pause", 8: "stop", 16: "return_home", 32: "fan_speed",
               64: "battery", 128: "status", 256: "send_command", 512: "locate",
               1024: "clean_spot", 2048: "map", 4096: "state", 8192: "start"},
    "alarm_control_panel": {1: "arm_home", 2: "arm_away", 4: "arm_night", 8: "trigger",
                            16: "arm_custom_bypass", 32: "arm_vacation"},
    "camera": {1: "on_off", 2: "stream"},
    "water_heater": {1: "target_temperature", 2: "operation_mode", 4: "away_mode", 8: "on_off"},
    "humidifier": {1: "modes"},
    "siren": {1: "turn_on", 2: "turn_off", 4: "tones", 8: "volume_set", 16: "duration"},
    "lawn_mower": {1: "start_mowing", 2: "pause", 4: "dock"},
    "update": {1: "install", 2: "specific_version", 4: "backup", 8: "progress", 16: "release_notes"},
    "remote": {1: "learn_command", 2: "delete_command", 4: "activity"},
}

# Services that are admin/maintenance actions, not dashboard controls.
NON_CONTROL_SERVICES = {"reload", "create", "remove", "set", "enable_motion_detection",
                        "disable_motion_detection", "snapshot", "record", "play_stream"}


def scrub(text):
    """Remove URL, host and token from any message before it is printed."""
    text = str(text)
    if TOKEN:
        text = text.replace(TOKEN, "<token>")
    if URL:
        text = text.replace(URL, "<ha-url>")
        host = urlparse(URL).hostname
        if host:
            text = text.replace(host, "<host>")
    return text


class HAWebSocket:
    def __init__(self):
        ws_url = ("wss://" if URL.startswith("https") else "ws://") + URL.split("://", 1)[1] + "/api/websocket"
        self.ws = websocket.create_connection(ws_url, timeout=30)
        self.next_id = 1
        hello = json.loads(self.ws.recv())
        if hello.get("type") != "auth_required":
            raise RuntimeError("Unexpected WebSocket greeting")
        self.ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
        auth = json.loads(self.ws.recv())
        if auth.get("type") != "auth_ok":
            raise RuntimeError("WebSocket authentication failed (token rejected)")
        self.ha_version = auth.get("ha_version", "unknown")

    def call(self, cmd, **params):
        if cmd not in ALLOWED_WS:
            raise RuntimeError(f"Refusing non-read command: {cmd}")
        msg_id = self.next_id
        self.next_id += 1
        self.ws.send(json.dumps({"id": msg_id, "type": cmd, **params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == msg_id and msg.get("type") == "result":
                if not msg.get("success"):
                    raise RuntimeError(f"{cmd} failed: {msg.get('error', {}).get('code')}")
                return msg["result"]

    def close(self):
        self.ws.close()


def check():
    if not URL or not TOKEN:
        sys.exit("HA_URL / HA_TOKEN not set. Use scripts/run-ha.ps1.")
    r = requests.get(URL + "/api/", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=15)
    print(f"REST /api/: HTTP {r.status_code} {r.json().get('message', '') if r.ok else ''}".strip())
    r.raise_for_status()
    ws = HAWebSocket()
    print(f"WebSocket auth: OK, Home Assistant {ws.ha_version}")
    user = ws.call("auth/current_user")
    print(f"Token user is admin: {user.get('is_admin')}")
    ws.close()


def decode_features(domain, bits):
    table = FEATURES.get(domain)
    if not table or not isinstance(bits, int):
        return []
    return [name for bit, name in table.items() if bits & bit]


SENSITIVE_WORDS = ("location", "geocoded", "ssid", "bssid", "vin", "registration_number",
                   "ip_address", "mac_address", "public_ip", "gps", "latitude", "longitude")


def is_sensitive(eid, domain):
    """Entities whose *values* would be sensitive; values are never stored, but flag them
    so they are kept off dashboards."""
    if domain in PRESENCE_DOMAINS:
        return True
    obj = eid.split(".", 1)[1]
    return any(obj == w or obj.endswith("_" + w) or f"_{w}_" in obj or obj.startswith(w + "_")
               for w in SENSITIVE_WORDS)


def availability(state_obj, reg):
    if reg and reg.get("disabled_by"):
        return f"disabled ({reg['disabled_by']})"
    if state_obj is None:
        return "no state (not loaded)"
    s = state_obj.get("state")
    if s == "unavailable":
        return "unavailable"
    if s == "unknown":
        return "unknown"
    return "available"


def role(domain, reg):
    cat = (reg or {}).get("entity_category")
    if cat:
        return cat  # "config" or "diagnostic"
    if domain == "camera":
        return "camera"
    if domain in READ_ONLY_DOMAINS:
        return "status"
    return "control"


def inventory():
    ws = HAWebSocket()
    unknowns = []

    def safe(cmd):
        try:
            return ws.call(cmd)
        except Exception as e:  # noqa: BLE001
            unknowns.append(f"`{cmd}` could not be read: {scrub(e)}")
            return None

    user = safe("auth/current_user") or {}
    areas = safe("config/area_registry/list") or []
    floors = safe("config/floor_registry/list") or []
    devices = safe("config/device_registry/list") or []
    entities = safe("config/entity_registry/list") or []
    states = safe("get_states") or []
    services = safe("get_services") or {}
    ws.close()

    floor_name = {f["floor_id"]: f.get("name") for f in floors}
    area_by_id = {a["area_id"]: a for a in areas}
    device_by_id = {d["id"]: d for d in devices}
    reg_by_eid = {e["entity_id"]: e for e in entities}
    state_by_eid = {s["entity_id"]: s for s in states}

    rows = []
    excluded = Counter()
    for eid in sorted(set(reg_by_eid) | set(state_by_eid)):
        domain = eid.split(".", 1)[0]
        if domain in EXCLUDED_DOMAINS:
            excluded[domain] += 1
            continue
        reg = reg_by_eid.get(eid)
        st = state_by_eid.get(eid)
        attrs = (st or {}).get("attributes", {})
        dev = device_by_id.get((reg or {}).get("device_id"))

        area_id = (reg or {}).get("area_id") or (dev or {}).get("area_id")
        area_source = "entity" if (reg or {}).get("area_id") else ("device" if area_id else "")
        area = area_by_id.get(area_id, {})

        name = (attrs.get("friendly_name") or (reg or {}).get("name")
                or (reg or {}).get("original_name") or eid)

        caps = {k: attrs[k] for k in CAPABILITY_ATTRS if k in attrs}
        for k in ("effect_list", "source_list", "options"):
            if isinstance(caps.get(k), list) and len(caps[k]) > 25:
                caps[k] = caps[k][:25] + [f"...(+{len(caps[k]) - 25} more)"]
        if attrs.get("code_format") is not None:
            caps["code_required"] = True
        feats = decode_features(domain, attrs.get("supported_features"))

        if domain in READ_ONLY_DOMAINS:
            controls = []
        else:
            controls = sorted(s for s in services.get(domain, {}) if s not in NON_CONTROL_SERVICES)

        dev_name = ""
        if dev:
            dev_name = dev.get("name_by_user") or dev.get("name") or ""

        rows.append({
            "floor": floor_name.get(area.get("floor_id"), "") if area else "",
            "room": area.get("name", "") or "(no area)",
            "area_source": area_source,
            "display_name": name,
            "entity_id": eid,
            "domain": domain,
            "role": role(domain, reg),
            "device": dev_name,
            "manufacturer": (dev or {}).get("manufacturer") or "",
            "model": (dev or {}).get("model") or "",
            "integration": (reg or {}).get("platform") or "",
            "features": feats,
            "controls": controls,
            "capabilities": caps,
            "availability": availability(st, reg),
            "hidden": bool((reg or {}).get("hidden_by")),
            "in_registry": reg is not None,
            "sensitive": is_sensitive(eid, domain),
        })

    OUT_DIR.mkdir(exist_ok=True)
    meta = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ha_version": ws.ha_version,
        "token_user_is_admin": user.get("is_admin"),
        "counts": {"areas": len(areas), "floors": len(floors), "devices": len(devices),
                   "registry_entities": len(entities), "state_entities": len(states),
                   "inventory_rows": len(rows)},
        "excluded_domains": dict(excluded),
        "could_not_determine": unknowns,
    }
    (OUT_DIR / "ha_inventory.json").write_text(
        json.dumps({"meta": meta,
                    "areas": [{"name": a.get("name"), "area_id": a["area_id"],
                               "floor": floor_name.get(a.get("floor_id"), "")} for a in areas],
                    "entities": rows}, indent=2, ensure_ascii=False), encoding="utf-8")

    cols = list(rows[0].keys()) if rows else []
    with open(OUT_DIR / "ha_inventory.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
                        for k, v in r.items()})

    write_summary(meta, rows)

    # Console summary (no values, no addresses)
    by_room = defaultdict(Counter)
    for r in rows:
        by_room[r["room"]][r["role"]] += 1
    print(json.dumps(meta, indent=2))
    print("\nRows per room (role counts):")
    for room in sorted(by_room):
        print(f"  {room}: {dict(by_room[room])}")
    print(f"\nWrote {OUT_DIR / 'ha_inventory.csv'} and ha_inventory.json")


def write_summary(meta, rows):
    """Human-readable per-room view of enabled, dashboard-relevant entities."""
    def md(s):
        return str(s).replace("|", "\\|")

    lines = [f"# Home Assistant inventory (sanitized)", "",
             f"Generated {meta['generated_utc']} from Home Assistant {meta['ha_version']} "
             f"(read-only, non-admin token). Values, addresses, coordinates and presence states are not recorded.", "",
             "| | count |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in meta["counts"].items()]
    avail = Counter(r["availability"].split(" ")[0] for r in rows)
    lines += ["", "Availability across all rows: " + ", ".join(f"{k} {v}" for k, v in avail.most_common()), ""]

    cams = [r for r in rows if r["domain"] == "camera"]
    lines += ["## Cameras (names only, no images or streams fetched)", "",
              "| Room | Display name | Entity ID | Features | Availability |", "|---|---|---|---|---|"]
    lines += [f"| {md(r['room'])} | {md(r['display_name'])} | `{r['entity_id']}` | {', '.join(r['features'])} | {r['availability']} |"
              for r in sorted(cams, key=lambda r: (r["availability"].startswith("disabled"), r["room"], r["entity_id"]))]

    live = [r for r in rows if not r["availability"].startswith("disabled")
            and r["role"] in ("control", "status") and r["domain"] != "camera"]
    by_room = defaultdict(list)
    for r in live:
        by_room[r["room"]].append(r)
    lines += ["", "## Controls and status sensors by room", "",
              "Enabled entities only, excluding config/diagnostic entities and cameras. Full list incl. "
              "disabled/diagnostic entities is in `ha_inventory.csv`. ⚠ = sensitive, keep off dashboards.", ""]
    for room in sorted(by_room, key=lambda x: (x == "(no area)", x)):
        lines += [f"### {room}", "", "| Role | Display name | Entity ID | Domain | Capabilities | Availability |",
                  "|---|---|---|---|---|---|"]
        for r in sorted(by_room[room], key=lambda x: (x["role"], x["domain"], x["entity_id"])):
            c = r["capabilities"]
            cap = [c.get("device_class", ""), c.get("unit_of_measurement", "")] + r["features"]
            if c.get("supported_color_modes"):
                cap.append("color modes: " + "/".join(c["supported_color_modes"]))
            if c.get("hvac_modes"):
                cap.append("hvac: " + "/".join(c["hvac_modes"]))
            if c.get("code_required"):
                cap.append("code required")
            flag = "⚠ " if r["sensitive"] else ""
            lines.append(f"| {r['role']} | {flag}{md(r['display_name'])} | `{r['entity_id']}` | {r['domain']} | "
                         f"{md(', '.join(x for x in cap if x))} | {r['availability']} |")
        lines.append("")
    (OUT_DIR / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")


ENTITY_RE = re.compile(r"\b(?:alarm_control_panel|binary_sensor|button|camera|climate|cover|fan|input_\w+|light|lock|media_player|number|person|remote|scene|script|select|sensor|siren|sun|switch|todo|update|vacuum|weather|zone|device_tracker|automation|valve|water_heater|humidifier|lawn_mower|event|image|calendar)\.[a-z0-9_]+\b")


def summarize_lovelace(cfg):
    """Card types, view layout types and referenced entity IDs only. Raw config is
    never stored or printed (it can contain URLs, IPs or other private values)."""
    card_types, view_types, entities = Counter(), Counter(), set()

    def walk(node):
        if isinstance(node, dict):
            t = node.get("type")
            if isinstance(t, str) and ("cards" in node or "entity" in node or "entities" in node or ":" in t
                                       or t.endswith(("card", "stack")) or t in ("tile", "heading", "entities", "grid", "picture-entity")):
                card_types[t] += 1
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str):
            entities.update(ENTITY_RE.findall(node))

    views = cfg.get("views", []) if isinstance(cfg, dict) else []
    for v in views:
        view_types[v.get("type", "masonry")] += 1
    walk(views)
    return {"views": [{"title": v.get("title", ""), "path": v.get("path", ""), "type": v.get("type", "masonry"),
                       "subview": bool(v.get("subview"))} for v in views],
            "view_types": dict(view_types), "card_types": dict(card_types.most_common()),
            "entity_refs": sorted(entities)}


def safe_resource(url):
    p = urlparse(url)
    if p.scheme in ("http", "https"):
        host = p.hostname or ""
        local = host.replace(".", "").isdigit() or host.endswith((".local", ".lan")) or host == urlparse(URL).hostname
        return ("<local-host>" if local else host) + p.path
    q = "?" + p.query if "hacstag" in p.query else ""
    return p.path + q


def dashboards():
    ws = HAWebSocket()
    out, notes = {"ha_version": ws.ha_version}, []

    def safe(cmd, **params):
        try:
            return ws.call(cmd, **params)
        except Exception as e:  # noqa: BLE001
            notes.append(f"`{cmd}{' ' + json.dumps(params) if params else ''}`: {scrub(e)}")
            return None

    cfg = safe("get_config") or {}
    out["ha_version"] = cfg.get("version", ws.ha_version)
    comps = set(cfg.get("components", []))
    out["relevant_components"] = sorted(c for c in comps if c in ("hacs", "lovelace", "frontend", "camera", "stream", "go2rtc", "webrtc"))
    dash = safe("lovelace/dashboards/list")
    out["dashboards"] = [{"title": d.get("title"), "url_path": d.get("url_path"), "mode": d.get("mode"),
                          "require_admin": d.get("require_admin"), "show_in_sidebar": d.get("show_in_sidebar")}
                         for d in (dash or [])]
    out["dashboard_configs"] = {}
    for path in [None] + [d["url_path"] for d in out["dashboards"]]:
        key = path or "(default Overview)"
        c = safe("lovelace/config", url_path=path, force=False)
        out["dashboard_configs"][key] = summarize_lovelace(c) if c else "not readable or auto-generated"
    res = safe("lovelace/resources")
    out["resources"] = [{"type": r.get("type"), "url": safe_resource(r.get("url", ""))} for r in (res or [])]
    themes = safe("frontend/get_themes") or {}
    out["themes"] = sorted((themes.get("themes") or {}).keys())
    out["default_theme"] = themes.get("default_theme")
    out["notes"] = notes
    ws.close()
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "dashboard_inspection.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("ha_version", "relevant_components", "dashboards", "resources", "themes", "default_theme", "notes")}, indent=2))
    for k, v in out["dashboard_configs"].items():
        if isinstance(v, dict):
            print(f"\n{k}: views={[(x['title'], x['type']) for x in v['views']]}\n  cards={v['card_types']}\n  entity refs={len(v['entity_refs'])}")
        else:
            print(f"\n{k}: {v}")


def pull_dashboard(url_path):
    """Read one dashboard's stored config (read-only) and save it as JSON for syncing.
    Refuses to save if it contains anything that looks like a URL host, IP or token."""
    ws = HAWebSocket()
    cfg = ws.call("lovelace/config", url_path=url_path, force=False)
    ws.close()
    raw = json.dumps(cfg, ensure_ascii=False)
    host = urlparse(URL).hostname or "\0"
    risky = re.findall(r"https?://[^\s\"']+|\b\d{1,3}(?:\.\d{1,3}){3}\b|eyJ[\w-]{10,}|access_token", raw)
    if host in raw or risky:
        sys.exit(f"Not saved: the dashboard contains {len(risky) + (host in raw)} URL/IP/token-like value(s). Review it in Home Assistant first.")
    out = HERE_ROOT / "ha-dashboard" / "live" / f"{url_path}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():  # keep one previous pull to roll back to
        out.with_name(f"{url_path}.previous.json").write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
    out.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    views = cfg.get("views", [])
    print(f"Pulled '{url_path}': {len(views)} views -> {out.relative_to(HERE_ROOT)}")


def diagnose_theme(name="Home Panel"):
    """Read-only: which version of a theme HA has loaded, and whether /local backgrounds are served."""
    ws = HAWebSocket()
    th = ws.call("frontend/get_themes")
    ws.close()
    t = (th.get("themes") or {}).get(name)
    print(f"Theme '{name}' loaded: {bool(t)}; default theme: {th.get('default_theme')}; dark theme: {th.get('default_dark_theme')}")
    urls = set()
    for mode, vals in ((t or {}).get("modes") or {}).items():
        print(f"  {mode}: {len(vals)} vars; hp-gold={vals.get('hp-gold')}; hp-radius={vals.get('hp-radius')}; "
              f"lovelace-background={'set' if vals.get('lovelace-background') else 'missing'}")
        m = re.search(r"url\('?(/local/[^')]+)'?\)", str(vals.get("hp-background", "")))
        if m:
            urls.add(m.group(1))
    for u in sorted(urls):
        try:
            r = requests.get(URL + u, timeout=15)
            print(f"  {u}: HTTP {r.status_code}, {len(r.content)} bytes, type {r.headers.get('content-type', '?')}")
        except Exception as e:  # noqa: BLE001
            print(f"  {u}: {scrub(e)}")


HERE_ROOT = Path(__file__).resolve().parent.parent

if __name__ == "__main__":
    try:
        if "--theme" in sys.argv:
            diagnose_theme()
        elif "--pull" in sys.argv:
            pull_dashboard(sys.argv[sys.argv.index("--pull") + 1])
        elif "--dashboards" in sys.argv:
            dashboards()
        elif "--inventory" in sys.argv:
            inventory()
        else:
            check()
    except Exception as e:  # noqa: BLE001
        sys.exit(f"ERROR: {type(e).__name__}: {scrub(e)}")
