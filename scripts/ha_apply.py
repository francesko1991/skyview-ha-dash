"""Narrow, approval-gated changes for the Home Panel "what to wear" feature.

Separate from ha_inventory.py (which stays read-only). Only the steps below exist; each
prints exactly what it would send, and only changes Home Assistant with --apply.

  run-apply.ps1 backup                      read-only: save current script + dashboard
  run-apply.ps1 <step> [--apply]            steps: helpers, script, automation, dashboard

Needs an admin token (the dashboard-reader user made admin temporarily).
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ha_inventory as h  # noqa: E402  (reuses the connection + scrubbing)

ROOT = Path(__file__).resolve().parent.parent
PAYLOADS = ROOT / "ha-dashboard" / "outfit" / "payloads"
BACKUPS = ROOT / "ha-dashboard" / "backups"
SCRIPT_KEY = "refresh_weather_clothing_advice"
AUTOMATION_KEY = "outfit_advice_keep_fresh"
DASHBOARD = "home-panel"

HELPERS = [
    ("input_text/create", {"name": "Outfit headline", "min": 0, "max": 40, "mode": "text", "icon": "mdi:hanger"}, "input_text.outfit_headline"),
    ("input_text/create", {"name": "Outfit icon", "min": 0, "max": 40, "mode": "text", "icon": "mdi:hanger"}, "input_text.outfit_icon"),
    ("input_datetime/create", {"name": "Outfit updated", "has_date": True, "has_time": True, "icon": "mdi:update"}, "input_datetime.outfit_updated"),
    ("input_datetime/create", {"name": "Work departure", "has_date": False, "has_time": True, "icon": "mdi:car-clock"}, "input_datetime.work_departure"),
]
DEPARTURE = "07:00:00"


def ws_raw(ws, msg_type, **params):
    """Send one message outside ha_inventory's read-only allowlist. Only called from the steps below."""
    msg_id = ws.next_id
    ws.next_id += 1
    ws.ws.send(json.dumps({"id": msg_id, "type": msg_type, **params}))
    while True:
        m = json.loads(ws.ws.recv())
        if m.get("id") == msg_id and m.get("type") == "result":
            if not m.get("success"):
                raise RuntimeError(f"{msg_type} failed: {m.get('error')}")
            return m.get("result")


def rest(method, path, body=None):
    r = requests.request(method, h.URL + path, headers={"Authorization": f"Bearer {h.TOKEN}"}, json=body, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path}: HTTP {r.status_code} {h.scrub(r.text[:200])}")
    return r.json() if r.content else None


def show(title, obj):
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    print(f"--- {title}")
    print(text[:1500] + ("\n  ... (truncated)" if len(text) > 1500 else ""))


def require_admin(ws):
    if not ws.call("auth/current_user").get("is_admin"):
        sys.exit("Token is not admin. Make dashboard-reader an Administrator first.")


def step_backup():
    ws = h.HAWebSocket()
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    ws.close()
    script = rest("GET", f"/api/config/script/config/{SCRIPT_KEY}")
    out = BACKUPS / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / f"script.{SCRIPT_KEY}.json").write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / f"dashboard.{DASHBOARD}.json").write_text(json.dumps(dash, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Backed up script ({len(script.get('sequence', []))} steps) and dashboard ({len(dash.get('views', []))} views) to {out.relative_to(ROOT)}")


def step_helpers(apply):
    ws = h.HAWebSocket()
    require_admin(ws)
    existing = {e["entity_id"] for e in ws.call("config/entity_registry/list")}
    for cmd, data, eid in HELPERS:
        if eid in existing:
            print(f"skip   {eid} (already exists)")
            continue
        show(f"{cmd} -> expect {eid}", data)
        if apply:
            res = ws_raw(ws, cmd, **data)
            print(f"created {eid} (helper id {res.get('id')})")
    call = {"domain": "input_datetime", "service": "set_datetime", "service_data": {"time": DEPARTURE},
            "target": {"entity_id": "input_datetime.work_departure"}}
    show("then call_service (set Work departure)", call)
    if apply:
        ws_raw(ws, "call_service", **call)
        print(f"set input_datetime.work_departure to {DEPARTURE}")
    ws.close()


def step_script(apply):
    ws = h.HAWebSocket()
    require_admin(ws)
    ws.close()
    body = json.loads((PAYLOADS / "script.json").read_text(encoding="utf-8"))
    show(f"POST /api/config/script/config/{SCRIPT_KEY}", body)
    if apply:
        print(rest("POST", f"/api/config/script/config/{SCRIPT_KEY}", body))


def step_automation(apply):
    ws = h.HAWebSocket()
    require_admin(ws)
    ws.close()
    body = json.loads((PAYLOADS / "automation.json").read_text(encoding="utf-8"))
    show(f"POST /api/config/automation/config/{AUTOMATION_KEY}", body)
    if apply:
        print(rest("POST", f"/api/config/automation/config/{AUTOMATION_KEY}", body))


def step_dashboard(apply):
    ws = h.HAWebSocket()
    require_admin(ws)
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    first = dash["views"][0]["sections"][0]
    if not (first.get("cards") and first["cards"][0].get("type") == "clock"):
        ws.close()
        sys.exit("Home view's first section is not the clock/header section anymore. Stopping without changes.")
    new = json.loads((PAYLOADS / "header_section.json").read_text(encoding="utf-8"))
    print(f"Replace views[0].sections[0] (currently {len(first['cards'])} cards: {[c['type'] for c in first['cards']]})")
    print(f"   with {len(new['cards'])} cards: {[c['type'] for c in new['cards']]}; all other sections and views unchanged")
    if apply:
        dash["views"][0]["sections"][0] = new
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    ws.close()


def step_waze(apply):
    """Add Waze Travel Time 'Work commute' (home zone -> work). The destination comes from the
    WORK_DEST environment variable of this process only; it is never written to disk or printed."""
    import os
    dest = os.environ.get("WORK_DEST", "").strip()
    if not dest:
        sys.exit("Set WORK_DEST for this run only (not saved anywhere).")
    form = {"name": "Work commute", "origin": "zone.home", "destination": dest, "region": "us"}
    show("config flow waze_travel_time (destination hidden)", {**form, "destination": "<work address>"})
    if not apply:
        return
    flow = rest("POST", "/api/config/config_entries/flow", {"handler": "waze_travel_time", "show_advanced_options": False})
    res = rest("POST", f"/api/config/config_entries/flow/{flow['flow_id']}", form)
    print("flow result:", res.get("type"), "| title:", res.get("title"), "| errors:", res.get("errors"))


def step_rename_commute(apply):
    """Rename the Waze sensor to the entity ID the script expects."""
    old, new = "sensor.waze_work_commute", "sensor.work_commute"
    print(f"config/entity_registry/update {old} -> {new}")
    if apply:
        ws = h.HAWebSocket(); require_admin(ws)
        ws_raw(ws, "config/entity_registry/update", entity_id=old, new_entity_id=new)
        ws.close()
        print("renamed")


AREA_FIXES = {"Homesite Back Deck": "Back Deck", "Front Gate": "Front Gate"}  # device name -> correct area name


def step_areas(apply):
    """Move the two mixed-up camera devices to their correct areas (device registry only)."""
    ws = h.HAWebSocket()
    require_admin(ws)
    areas = {a["name"]: a["area_id"] for a in ws.call("config/area_registry/list")}
    names = {a_id: n for n, a_id in areas.items()}
    for d in ws.call("config/device_registry/list"):
        name = d.get("name_by_user") or d.get("name")
        if name in AREA_FIXES and d.get("model") == "B800":
            target = areas[AREA_FIXES[name]]
            print(f"device '{name}': area {names.get(d.get('area_id'), 'none')} -> {AREA_FIXES[name]}"
                  + ("  (already correct)" if d.get("area_id") == target else ""))
            if apply and d.get("area_id") != target:
                ws_raw(ws, "config/device_registry/update", device_id=d["id"], area_id=target)
                print("  updated")
    ws.close()


def step_views(apply):
    """Replace only the listed views (by path) with payloads/views.json; Home and Rooms stay untouched."""
    new_views = json.loads((PAYLOADS / "views.json").read_text(encoding="utf-8"))
    protected = {"home", "rooms"}
    if protected & set(new_views):
        sys.exit("Refusing: payload contains a protected view (home/rooms).")
    ws = h.HAWebSocket()
    require_admin(ws)
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    paths = [v.get("path") for v in dash["views"]]
    for p, v in new_views.items():
        print(f"{'replace' if p in paths else 'MISSING'} view {p}: {len(v.get('sections', []))} sections")
    if apply:
        if any(p not in paths for p in new_views):
            ws.close()
            sys.exit("A target view is missing; stopping without changes.")
        dash["views"] = [new_views.get(v.get("path"), v) for v in dash["views"]]
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    ws.close()


def step_garage_hero(apply):
    """Replace only the Battery (mushroom-template) card in the Home 'Garage · Polestar 2' section."""
    new = json.loads((PAYLOADS / "garage_hero.json").read_text(encoding="utf-8"))
    ws = h.HAWebSocket()
    require_admin(ws)
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    hits = [(si, ci) for si, s in enumerate(dash["views"][0].get("sections", []))
            if s.get("cards") and s["cards"][0].get("heading", "").startswith("Garage")
            for ci, c in enumerate(s["cards"])
            if c.get("type") == "custom:mushroom-template-card" and c.get("entity") == "sensor.polestar_2_battery_charge_level"]
    if len(hits) != 1:
        ws.close()
        sys.exit(f"Expected exactly one Battery card in the Garage section, found {len(hits)}; stopping.")
    si, ci = hits[0]
    old = dash["views"][0]["sections"][si]["cards"][ci]
    changed = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
    print(f"Home section {si}, card {ci}: fields changing: {changed}")
    if apply:
        dash["views"][0]["sections"][si]["cards"][ci] = new
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    ws.close()


def step_garage_lights(apply):
    """Swap the Home 'Garage' section for the swipeable Garage/Lights section and drop the old Lights section."""
    new = json.loads((PAYLOADS / "garage_lights_section.json").read_text(encoding="utf-8"))
    ws = h.HAWebSocket()
    require_admin(ws)
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    secs = dash["views"][0]["sections"]
    head = lambda s: (s.get("cards") or [{}])[0].get("heading", "")
    g = [i for i, s in enumerate(secs) if head(s).startswith("Garage")]
    lt = [i for i, s in enumerate(secs) if head(s) == "Lights and plugs"]
    if len(g) != 1 or len(lt) != 1:
        ws.close()
        sys.exit(f"Expected one Garage and one 'Lights and plugs' section, found {len(g)} and {len(lt)}; stopping.")
    print(f"Replace Home section {g[0]} (Garage, {len(secs[g[0]]['cards'])} cards) with the swipe section; "
          f"remove section {lt[0]} (Lights and plugs, {len(secs[lt[0]]['cards'])} cards). Home sections {len(secs)} -> {len(secs) - 1}")
    if apply:
        secs[g[0]] = new
        del secs[lt[0]]
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    ws.close()


def step_swipe_style(apply):
    """Update only the card_mod style of the Garage/Lights horizontal-stack on Home."""
    style = json.loads((PAYLOADS / "garage_lights_section.json").read_text(encoding="utf-8"))["cards"][0]["card_mod"]["style"]
    ws = h.HAWebSocket()
    require_admin(ws)
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    hits = [c for s in dash["views"][0]["sections"] for c in s.get("cards", [])
            if c.get("type") == "horizontal-stack" and "Garage" in json.dumps(c.get("cards", []))[:2000]]
    if len(hits) != 1:
        ws.close()
        sys.exit(f"Expected one Garage swipe card, found {len(hits)}; stopping.")
    print("new card_mod style:\n" + style)
    if apply:
        hits[0]["card_mod"] = {"style": style}
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    ws.close()


SWIPE_FULL = """:host #root {
  overflow-x: auto !important;
  scroll-snap-type: x mandatory;
  scrollbar-width: none;
  gap: 16px !important;
  align-items: flex-start;
  overscroll-behavior-x: contain;
}
:host #root::-webkit-scrollbar {
  display: none;
}
:host #root > hui-card > * {
  flex: 0 0 100% !important;
  max-width: 100%;
  min-width: 0;
  scroll-snap-align: start;
}
"""


def step_swipe_tidy(apply):
    """Full-width swipe pages (no peek) and remove the 'Lights'/'Garage' hint badges on the two page headings."""
    ws = h.HAWebSocket()
    require_admin(ws)
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    hits = [c for s in dash["views"][0]["sections"] for c in s.get("cards", [])
            if c.get("type") == "horizontal-stack" and "Garage" in json.dumps(c.get("cards", []))[:2000]]
    if len(hits) != 1:
        ws.close()
        sys.exit(f"Expected one Garage swipe card, found {len(hits)}; stopping.")
    stack = hits[0]
    removed = []
    for page in stack["cards"]:
        head = page.get("cards", [{}])[0]
        if head.get("type") == "heading":
            keep = [b for b in head.get("badges", []) if not (b.get("type") == "button" and b.get("text") in ("Lights", "Garage"))]
            removed += [f"{head['heading']}: '{b['text']}' badge" for b in head.get("badges", []) if b not in keep]
            head["badges"] = keep
    print("remove:", removed)
    print("style: pages flex 0 0 100% (no peek), gap 16px")
    if apply:
        stack["card_mod"] = {"style": SWIPE_FULL}
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    ws.close()


LAUNDRY = [f"light.laundry_light_{i}" for i in range(1, 7)]


def step_laundry_group(apply):
    """Create a Light group helper 'Laundry lights' (light.laundry_lights) via the group config flow."""
    form = {"name": "Laundry lights", "entities": LAUNDRY, "hide_members": False, "all": False}
    show("group config flow: menu 'light', then form", form)
    if not apply:
        return
    ws = h.HAWebSocket(); require_admin(ws)
    existing = {e["entity_id"] for e in ws.call("config/entity_registry/list")}
    ws.close()
    if "light.laundry_lights" in existing:
        print("skip: light.laundry_lights already exists")
        return
    flow = rest("POST", "/api/config/config_entries/flow", {"handler": "group", "show_advanced_options": False})
    step = rest("POST", f"/api/config/config_entries/flow/{flow['flow_id']}", {"next_step_id": "light"})
    res = rest("POST", f"/api/config/config_entries/flow/{step['flow_id']}", form)
    print("flow result:", res.get("type"), "| title:", res.get("title"), "| errors:", res.get("errors"))


def step_lights_page(apply):
    """Replace only the Lights page (second page) of the Garage/Lights swipe card."""
    new = json.loads((PAYLOADS / "lights_page.json").read_text(encoding="utf-8"))
    ws = h.HAWebSocket()
    require_admin(ws)
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    hits = [c for s in dash["views"][0]["sections"] for c in s.get("cards", [])
            if c.get("type") == "horizontal-stack" and len(c.get("cards", [])) == 2
            and c["cards"][1].get("cards", [{}])[0].get("heading") == "Lights & plugs"]
    if len(hits) != 1:
        ws.close()
        sys.exit(f"Expected one swipe card with a 'Lights & plugs' page, found {len(hits)}; stopping.")
    old = hits[0]["cards"][1]
    print(f"Replace Lights page ({len(old['cards'])} cards) with new page ({len(new['cards'])} cards: "
          f"{[c.get('heading') or c['type'] for c in new['cards']]})")
    if apply:
        hits[0]["cards"][1] = new
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    ws.close()


def step_odometer_miles(apply):
    """Show sensor.polestar_2_odometer in miles (entity display option; keeps other sensor options)."""
    eid = "sensor.polestar_2_odometer"
    ws = h.HAWebSocket()
    require_admin(ws)
    entry = ws_raw(ws, "config/entity_registry/get", entity_id=eid)
    current = (entry.get("options") or {}).get("sensor") or {}
    new = {**current, "unit_of_measurement": "mi"}
    print(f"{eid} sensor options: {current} -> {new}")
    if apply:
        ws_raw(ws, "config/entity_registry/update", entity_id=eid, options_domain="sensor", options=new)
        print("updated")
    ws.close()


def step_swipe_pages(apply):
    """Replace both pages of the Garage/Lights swipe card (style and other cards untouched)."""
    pages = json.loads((PAYLOADS / "swipe_pages.json").read_text(encoding="utf-8"))
    ws = h.HAWebSocket()
    require_admin(ws)
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    hits = [c for s in dash["views"][0]["sections"] for c in s.get("cards", [])
            if c.get("type") == "horizontal-stack" and len(c.get("cards", [])) == 2
            and c["cards"][1].get("cards", [{}])[0].get("heading") == "Lights & plugs"]
    if len(hits) != 1:
        ws.close()
        sys.exit(f"Expected one Garage/Lights swipe card, found {len(hits)}; stopping.")
    for name, i in (("garage", 0), ("lights", 1)):
        print(f"{name} page: {len(hits[0]['cards'][i]['cards'])} cards -> {len(pages[name]['cards'])} cards")
    if apply:
        hits[0]["cards"] = [pages["garage"], pages["lights"]]
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    ws.close()


RENAMES = {  # entity -> new display name (entity IDs are NOT changed)
    "light.kitchen_kitchen_main_lights_2": "Kitchen Ceiling Zone 1",
    "light.kitchen_kitchen_main_lights_zone1": "Kitchen Ceiling Zone 2",
    "switch.great_room_great_room_main_lights_zone_2": "Great Room Main Lights",
    "switch.wemo_mini": "Great Room Fireplace",
    "light.laundry_lights": "Laundry Lights",
    "light.laundry_light_3": "Laundry Light 3", "light.laundry_light_4": "Laundry Light 4",
    "light.laundry_light_5": "Laundry Light 5", "light.laundry_light_6": "Laundry Light 6",
    "light.living_room_lamp": "Living Room Lamp",
    "light.bedroom_hallway_light": "Bedroom Hallway Light",
    "switch.night_light_socket": "Night Light Socket",
}
EXTRA = {"switch.wemo_mini": {"icon": "mdi:fireplace", "area": "Great Room"}}


def step_renames(apply):
    """Rename entity display names (name field only) and give the fireplace plug an icon + Great Room area."""
    ws = h.HAWebSocket()
    require_admin(ws)
    areas = {a["name"]: a["area_id"] for a in ws.call("config/area_registry/list")}
    reg = {e["entity_id"]: e for e in ws.call("config/entity_registry/list")}
    st = {s["entity_id"]: s["attributes"].get("friendly_name") for s in ws.call("get_states")}
    for eid, name in RENAMES.items():
        if eid not in reg:
            print(f"skip {eid}: not found")
            continue
        upd = {"name": name}
        extra = EXTRA.get(eid, {})
        if "icon" in extra:
            upd["icon"] = extra["icon"]
        if "area" in extra:
            upd["area_id"] = areas[extra["area"]]
        print(f"{eid:48} '{st.get(eid)}' -> '{name}'" + (f"  +icon {extra['icon']} +area {extra['area']}" if extra else ""))
        if apply:
            ws_raw(ws, "config/entity_registry/update", entity_id=eid, **upd)
    if apply:
        print("renamed")
    ws.close()


BLINDS = {  # Tuya Tap-to-Run scenes -> (chip label, icon). Scenes only send a command; no position feedback.
    "scene.blinds_open": ("Open Blinds", "mdi:blinds-open"),
    "scene.blinds_close": ("Close Blinds", "mdi:blinds"),
}


def _blind_chip(eid, label, icon):
    # No icon_color: neutral on purpose, since the dashboard cannot know where the blinds are.
    return {"type": "template", "entity": eid, "content": label, "icon": icon,
            "tap_action": {"action": "perform-action", "perform_action": "scene.turn_on", "target": {"entity_id": eid}},
            "hold_action": {"action": "none"}}


def step_blinds(apply):
    """Give the blind scenes an icon + Great Room area, and add Open/Close chips to the Living & Great Room panel."""
    ws = h.HAWebSocket()
    require_admin(ws)
    areas = {a["name"]: a["area_id"] for a in ws.call("config/area_registry/list")}
    reg = {e["entity_id"]: e for e in ws.call("config/entity_registry/list")}
    missing = [e for e in BLINDS if e not in reg]
    if missing:
        ws.close()
        sys.exit(f"Scene(s) not found: {missing}; stopping.")
    for eid, (_, icon) in BLINDS.items():
        print(f"{eid:22} area {reg[eid].get('area_id')} -> great_room, icon {reg[eid].get('icon')} -> {icon}")
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    hits = [c for s in dash["views"][0]["sections"] for c in s.get("cards", [])
            if c.get("type") == "horizontal-stack" and len(c.get("cards", [])) == 2
            and c["cards"][1].get("cards", [{}])[0].get("heading") == "Lights & plugs"]
    panels = []
    if len(hits) == 1:
        grids = [g for g in hits[0]["cards"][1]["cards"] if g.get("type") == "grid"]
        panels = [p for g in grids for p in g["cards"] if p["cards"][0].get("heading") == "Living & Great Room"]
    if len(panels) != 1:
        ws.close()
        sys.exit(f"Expected one 'Living & Great Room' panel on the Lights page, found {len(panels)}; stopping.")
    chips = panels[0]["cards"][1]["chips"]
    if any(c.get("entity") in BLINDS for c in chips):
        ws.close()
        sys.exit("Blind chips are already on the panel; nothing to do.")
    at = next(i for i, c in enumerate(chips) if c.get("entity") == "switch.wemo_mini") + 1
    new = [_blind_chip(e, lbl, ic) for e, (lbl, ic) in BLINDS.items()]
    print(f"Living & Great Room chips: {[c.get('content') or c['chip']['content'].split('{')[0] for c in chips]}")
    print(f"  insert after Fireplace: {[c['content'] for c in new]} (tap = scene.turn_on, hold = nothing)")
    if apply:
        for eid, (_, icon) in BLINDS.items():
            ws_raw(ws, "config/entity_registry/update", entity_id=eid, icon=icon, area_id=areas["Great Room"])
        chips[at:at] = new
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("scenes updated, dashboard saved")
    ws.close()


def _blind_button(eid, action):
    # Label is the action ("Open"), "Blinds" underneath, so it can't be read as the blinds' state or the door's.
    run = {"action": "perform-action", "perform_action": "scene.turn_on", "target": {"entity_id": eid}}
    return {"type": "custom:mushroom-template-card", "entity": eid, "primary": action, "secondary": "Blinds",
            "icon": BLINDS[eid][1], "color": "var(--hp-sky)", "vertical": True,
            "tap_action": run, "icon_tap_action": run, "hold_action": {"action": "none"},
            "grid_options": {"columns": 3, "rows": 2}}


def step_blinds_security(apply):
    """Move the blind buttons from the Lights page into the Security section, beside the Front Door."""
    ws = h.HAWebSocket()
    require_admin(ws)
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    secs = [s for s in dash["views"][0]["sections"] if s.get("cards") and s["cards"][0].get("heading") == "Security"]
    if len(secs) != 1:
        ws.close()
        sys.exit(f"Expected one Security section, found {len(secs)}; stopping.")
    cards = secs[0]["cards"]
    if any(c.get("entity") in BLINDS for c in cards):
        ws.close()
        sys.exit("Blind buttons are already in the Security section; nothing to do.")
    lock_i = next(i for i, c in enumerate(cards) if c.get("entity") == "lock.front_door")
    lock = cards[lock_i]
    new_lock = {**lock, "vertical": True, "grid_options": {"columns": 6, "rows": 2}}
    buttons = [_blind_button("scene.blinds_open", "Open"), _blind_button("scene.blinds_close", "Close")]
    # Old chips on the Lights page (added by step "blinds")
    chip_lists = [p["cards"][1]["chips"] for s in dash["views"][0]["sections"] for c in s.get("cards", [])
                  if c.get("type") == "horizontal-stack" for page in c.get("cards", []) for g in page.get("cards", [])
                  if g.get("type") == "grid" for p in g["cards"]
                  if p.get("cards") and p["cards"][0].get("heading") == "Living & Great Room"]
    old_chips = sum(1 for cl in chip_lists for c in cl if c.get("entity") in BLINDS)
    print(f"Front Door tile: grid {lock.get('grid_options')} -> {new_lock['grid_options']}, vertical layout")
    print(f"Add beside it: {[b['primary'] + ' ' + b['secondary'] for b in buttons]} (3+3 columns, sky blue, tap = run scene)")
    print(f"Lights page: remove {old_chips} blind chip(s) from Living & Great Room")
    if apply:
        cards[lock_i:lock_i + 1] = [new_lock, *buttons]
        for cl in chip_lists:
            cl[:] = [c for c in cl if c.get("entity") not in BLINDS]
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    ws.close()


SONY = {  # entity -> (new name, hide?)   All in Great Room. Hidden entities keep working.
    "media_player.great_room_sony_tv": ("Sony TV", False),
    "remote.sony_tv": ("Sony TV Remote", False),
    "media_player.sony_tv": ("Sony TV Cast", False),
    "button.great_room_sony_tv_restart": ("Sony TV Restart", False),
    "button.great_room_sony_tv_terminate_apps": ("Sony TV Close Apps", False),
    "remote.great_room_sony_tv": ("Sony TV Remote (Bravia)", True),
    "media_player.sony_tv_2": ("Sony TV (Android TV)", True),
    "media_player.sony_tv_3": ("Sony TV (DLNA)", True),
}
SONY_DEVICES = {  # an entity on the device -> new device name
    "media_player.great_room_sony_tv": "Sony TV",
    "media_player.sony_tv": "Sony TV · Cast",
    "media_player.sony_tv_2": "Sony TV · Android Remote",
    "media_player.sony_tv_3": "Sony TV · DLNA",
}


def step_sony(apply):
    """Rename the Sony TV entities/devices by role, put them all in Great Room, hide the duplicates."""
    ws = h.HAWebSocket()
    require_admin(ws)
    area = {a["name"]: a["area_id"] for a in ws.call("config/area_registry/list")}["Great Room"]
    reg = {e["entity_id"]: e for e in ws.call("config/entity_registry/list")}
    devs = {d["id"]: d for d in ws.call("config/device_registry/list")}
    missing = [e for e in {**SONY, **SONY_DEVICES} if e not in reg]
    if missing:
        ws.close()
        sys.exit(f"Not found: {missing}; stopping.")
    before = {"entities": {e: {k: reg[e].get(k) for k in ("name", "area_id", "hidden_by")} for e in SONY},
              "devices": {}}
    ent_upd = {}
    for eid, (name, hide) in SONY.items():
        e = reg[eid]
        upd = {"name": name, "hidden_by": "user" if hide else None}
        if e.get("area_id") not in (None, area):
            upd["area_id"] = area  # entity overrides its device's area; keep it consistent
        ent_upd[eid] = upd
        print(f"entity {eid:42} '{e.get('name') or e.get('original_name')}' -> '{name}'" + ("  HIDE" if hide else ""))
    dev_upd = {}
    for eid, name in SONY_DEVICES.items():
        d = devs[reg[eid]["device_id"]]
        before["devices"][d["id"]] = {"name_by_user": d.get("name_by_user"), "area_id": d.get("area_id")}
        dev_upd[d["id"]] = {"name_by_user": name, "area_id": area}
        print(f"device {(d.get('name_by_user') or d.get('name'))!r:14} -> {name!r:28} area {d.get('area_id')} -> {area}")
    if apply:
        out = BACKUPS / datetime.now().strftime("%Y%m%d-%H%M%S")
        out.mkdir(parents=True, exist_ok=True)
        (out / "sony_registry_before.json").write_text(json.dumps(before, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"previous names/areas/hidden saved to {out.relative_to(ROOT)}")
        for did, upd in dev_upd.items():
            ws_raw(ws, "config/device_registry/update", device_id=did, **upd)
        for eid, upd in ent_upd.items():
            ws_raw(ws, "config/entity_registry/update", entity_id=eid, **upd)
        print("updated")
    ws.close()


CARD_TIMER = {"name": "Outfit card", "duration": "00:05:00", "restore": False, "icon": "mdi:timer-sand"}
CARD_AUTOMATION_KEY = "outfit_card_show_5_minutes"
CARD_AUTOMATION = {  # event-driven: runs only when new advice lands, never on a schedule
    "alias": "Outfit card: show for 5 minutes",
    "description": "Starts the 5-minute Outfit card timer whenever the what-to-wear advice is refreshed. "
                   "The dashboard card is visible only while the timer is running.",
    "mode": "restart",
    "triggers": [{"trigger": "state", "entity_id": "input_datetime.outfit_updated",
                  "not_to": ["unknown", "unavailable"]}],
    "conditions": [],
    "actions": [{"action": "timer.start", "target": {"entity_id": "timer.outfit_card"}}],
}
SHOWING_COND = {"condition": "state", "entity": "timer.outfit_card", "state": "active"}


def step_outfit_stale(apply):
    """Show the what-to-wear card only for 5 minutes after each refresh (automatic, or the header chip's tap)."""
    ws = h.HAWebSocket()
    require_admin(ws)
    existing = {e["entity_id"] for e in ws.call("config/entity_registry/list")}
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    head = dash["views"][0]["sections"][0]["cards"]
    card = next((c for c in head if c.get("entity") == "input_text.weather_clothing_advice"), None)
    if not card:
        ws.close()
        sys.exit("What-to-wear card not found; stopping.")
    print("1. timer helper -> timer.outfit_card" + ("  (exists, skip)" if "timer.outfit_card" in existing else ""))
    show("   timer/create", CARD_TIMER)
    print(f"2. automation -> {CARD_AUTOMATION_KEY}")
    show(f"   POST /api/config/automation/config/{CARD_AUTOMATION_KEY}", CARD_AUTOMATION)
    print("3. card visibility: add", SHOWING_COND)
    print("   (header chip unchanged: tap still refreshes)")
    if not apply:
        ws.close()
        return
    if "timer.outfit_card" not in existing:
        ws_raw(ws, "timer/create", **CARD_TIMER)
        print("created timer.outfit_card")
    print("automation:", rest("POST", f"/api/config/automation/config/{CARD_AUTOMATION_KEY}", CARD_AUTOMATION))
    ents = {e["entity_id"] for e in ws.call("config/entity_registry/list")}
    if "timer.outfit_card" not in ents:
        ws.close()
        sys.exit(f"Timer got a different entity id: {sorted(e for e in ents if e.startswith('timer.'))}; dashboard left unchanged.")
    if SHOWING_COND not in card.setdefault("visibility", []):
        card["visibility"].append(SHOWING_COND)
    ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
    print("dashboard saved")
    for s in ws.call("get_states"):
        if s["entity_id"] == "timer.outfit_card" or s["attributes"].get("id") == CARD_AUTOMATION_KEY:
            print(f"  {s['entity_id']}: {s['state']}")
    ws.close()


ROW_HEIGHT = "ha-card {\n  height: 120px !important;\n}\n"  # = rows 2 in a sections grid, so both pages match
THERMOSTATS = [("climate.great_room_thermostat", "Great Room"), ("climate.living_room_thermostat", "Living Room")]


def _dots(text):
    return {"type": "markdown", "text_only": True, "content": text, "card_mod": {"style": (
        "ha-card {\n  text-align: center;\n  color: var(--secondary-text-color);\n  letter-spacing: 6px;\n  font-size: 12px;\n}\n")}}


def _thermostat_card(eid, name):
    s = f"'{eid}'"
    act = f"state_attr({s}, 'hvac_action')"
    return {
        "type": "custom:mushroom-template-card", "entity": eid, "primary": name, "vertical": True,
        "icon": "mdi:thermostat",
        # amber heating, sky cooling, ink when on but idle, muted when off/unavailable
        "color": (f"{{% set a = {act} %}}{{% if a == 'heating' %}}var(--hp-warn){{% elif a == 'cooling' %}}var(--hp-sky)"
                  f"{{% elif states({s}) in ['off', 'unavailable', 'unknown'] %}}var(--hp-muted){{% else %}}var(--hp-ink){{% endif %}}"),
        "badge_icon": (f"{{% set a = {act} %}}{{{{ 'mdi:fire' if a == 'heating' else ('mdi:snowflake' if a == 'cooling' else '') }}}}"),
        "badge_color": (f"{{% set a = {act} %}}{{{{ 'var(--hp-warn)' if a == 'heating' else 'var(--hp-sky)' }}}}"),
        "secondary": (f"{{% set c = state_attr({s}, 'current_temperature') %}}{{% set t = state_attr({s}, 'temperature') %}}"
                      f"{{% set hu = state_attr({s}, 'current_humidity') %}}"
                      f"{{{{ (c | round(1) ~ '°') if is_number(c) else '—' }}}}"
                      f"{{% if states({s}) == 'off' %}} · Off{{% elif is_number(t) %}} → {{{{ t | round(0) | int }}}}°{{% endif %}}"
                      f"{{% if is_number(hu) %}} · {{{{ hu | int }}}}%{{% endif %}}"),
        "tap_action": {"action": "more-info"}, "icon_tap_action": {"action": "more-info"}, "hold_action": {"action": "none"},
        "card_mod": {"style": ROW_HEIGHT},
    }


def step_security_swipe(apply):
    """Turn the Security section into a Security <-> Climate swipe card and remove the separate Climate section."""
    ws = h.HAWebSocket()
    require_admin(ws)
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    secs = dash["views"][0]["sections"]
    sec_i = [i for i, s in enumerate(secs) if s.get("cards") and s["cards"][0].get("heading") == "Security"]
    clim_i = [i for i, s in enumerate(secs) if s.get("cards") and s["cards"][0].get("heading") == "Climate"]
    garage = [c for s in secs for c in s.get("cards", []) if c.get("type") == "horizontal-stack"]
    if len(sec_i) != 1 or len(clim_i) != 1 or len(garage) != 1:
        ws.close()
        sys.exit(f"Expected one Security, one Climate section and one swipe card; found {len(sec_i)}, {len(clim_i)}, {len(garage)}. Stopping.")
    old = secs[sec_i[0]]["cards"]
    heading = old[0]
    lock = next(c for c in old if c.get("entity") == "lock.front_door")
    blinds = [c for c in old if c.get("entity") in BLINDS]
    warning = next(c for c in old if c.get("type") == "markdown")
    strip = lambda c: {k: v for k, v in c.items() if k != "grid_options"}  # noqa: E731
    lock_card = {**strip(lock), "vertical": True, "card_mod": {"style": ROW_HEIGHT}}
    blind_cards = [{**strip(b), "card_mod": {"style": ROW_HEIGHT}} for b in blinds]
    security_page = {"type": "vertical-stack", "cards": [
        heading,
        {"type": "grid", "columns": 2, "square": False, "cards": [
            lock_card, {"type": "grid", "columns": 2, "square": False, "cards": blind_cards}]},
        warning,
        _dots("●  ○")]}
    climate_heading = {"type": "heading", "heading": "Climate", "heading_style": "title", "icon": "mdi:thermostat", "badges": [
        {"type": "entity", "entity": "sensor.bedroom_sensor_temperature", "icon": "mdi:bed", "show_state": True,
         "show_icon": True, "tap_action": {"action": "more-info"}},
        {"type": "entity", "entity": "sensor.living_room_thermostat_temperature", "icon": "mdi:sofa", "show_state": True,
         "show_icon": True, "tap_action": {"action": "more-info"}}]}
    climate_page = {"type": "vertical-stack", "cards": [
        climate_heading,
        {"type": "grid", "columns": 2, "square": False, "cards": [_thermostat_card(e, n) for e, n in THERMOSTATS]},
        _dots("○  ●")]}
    swipe = {"type": "horizontal-stack", "cards": [security_page, climate_page],
             "card_mod": garage[0]["card_mod"], "grid_options": {"columns": "full"}}
    removed = [c.get("entity") for c in secs[clim_i[0]]["cards"][1:]]
    print(f"Security section: {len(old)} cards -> 1 swipe card (pages: Security, Climate); same swipe style as Garage/Lights")
    print(f"  Security page: heading, [Front Door | Open, Close blinds] at 120px, lock warning (when unknown), dots")
    print(f"  Climate page: heading + badges Bedroom/Living Room temp, [{' | '.join(n for _, n in THERMOSTATS)}], dots")
    print(f"Remove Climate section (section {clim_i[0]}): {removed}")
    show("climate card (Great Room)", _thermostat_card(*THERMOSTATS[0]))
    if apply:
        secs[sec_i[0]] = {**{k: v for k, v in secs[sec_i[0]].items() if k != "cards"}, "cards": [swipe]}
        del secs[clim_i[0]]
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    else:
        out = ROOT / "ha-dashboard" / "outfit" / "payloads" / "security_swipe.json"
        out.write_text(json.dumps(swipe, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"preview saved to {out.relative_to(ROOT)}")
    ws.close()


def step_roborock_maps(apply):
    """Ask the Roborock integration for its maps and room (segment) IDs. Moves nothing; saves the room list."""
    call = {"domain": "roborock", "service": "get_maps", "service_data": {},
            "target": {"entity_id": "vacuum.living_room_vacuum"}, "return_response": True}
    show("call_service (response only)", call)
    if not apply:
        return
    ws = h.HAWebSocket(); require_admin(ws)
    res = ws_raw(ws, "call_service", **call)
    ws.close()
    maps = (res or {}).get("response", {}).get("vacuum.living_room_vacuum", {}).get("maps", [])
    out = ROOT / "inventory" / "roborock_rooms.json"
    out.write_text(json.dumps(maps, indent=2, ensure_ascii=False), encoding="utf-8")
    for m in maps:
        print(f"map {m.get('flag')}: {m.get('name')!r}")
        for seg, name in (m.get("rooms") or {}).items():
            print(f"   segment {seg}: {name}")
    print(f"saved to {out.relative_to(ROOT)}")


MEDIA_AREAS = {  # user-confirmed 2026-09-26; set on the device (entities inherit)
    "media_player.samsung_tv": "Bedroom", "media_player.soundbar": "Great Room",
    "media_player.kitchen_display": "Kitchen", "media_player.master_bedroom_speaker": "Bedroom",
    "media_player.office_speaker": "Office", "media_player.greatroom_display": "Great Room",
}
VAC = "vacuum.living_room_vacuum"
VS = "sensor.living_room_vacuum_"
ROOMS = {  # from roborock.get_maps (inventory/roborock_rooms.json); segment IDs are per map
    "Homesite Upstairs": [(18, "Living Room"), (17, "Kitchen"), (20, "Office"), (23, "Master Bedroom"), (22, "Bathroom"),
                         (19, "New Bathroom"), (24, "Hallway"), (16, "Corridor")],
    "Homesite Downstairs": [(16, "Great Room")],
}
CARE = [("main_brush", "Main brush"), ("side_brush", "Side brush"), ("filter", "Filter"), ("sensor", "Sensors")]
PLAYERS = [  # entity, display name, kind
    ("media_player.great_room_sony_tv", "Sony TV", "tv"), ("media_player.sony_tv", "Sony TV", "cast"),
    ("media_player.samsung_tv", "Samsung TV", "tv"), ("media_player.soundbar", "Soundbar", "speaker"),
    ("media_player.master_bedroom_speaker", "Bedroom", "speaker"), ("media_player.office_speaker", "Office", "speaker"),
    ("media_player.kitchen_display", "Kitchen", "display"), ("media_player.greatroom_display", "Great Room", "display"),
    ("media_player.home_sync", "Everywhere", "group"),
]
ICONS = {"tv": ("mdi:television", "mdi:television-off"), "speaker": ("mdi:speaker", "mdi:speaker-off"),
         "display": ("mdi:tablet", "mdi:tablet"), "group": ("mdi:speaker-multiple", "mdi:speaker-multiple")}


def _run(action, data=None, target=VAC, confirm=None):
    a = {"action": "perform-action", "perform_action": action, "target": {"entity_id": target}}
    if data:
        a["data"] = data
    if confirm:
        a["confirmation"] = {"text": confirm}
    return a


def _chip(content, icon, tap, hold=None, icon_color=None, entity=None):
    c = {"type": "template", "content": content, "icon": icon, "tap_action": tap, "hold_action": hold or {"action": "none"}}
    if entity:
        c["entity"] = entity
    if icon_color:
        c["icon_color"] = icon_color
    return c


def _when(chip, *conds):
    return {"type": "conditional", "conditions": list(conds), "chip": chip}


def _panel(label, chips, heading_mod, panel_mod):
    return {"type": "vertical-stack", "card_mod": panel_mod, "cards": [
        {"type": "heading", "heading": label, "heading_style": "subtitle", "card_mod": heading_mod},
        {"type": "custom:mushroom-chips-card", "alignment": "start", "chips": chips}]}


def _cleaning_page(heading_mod, panel_mod):
    err = f"states('{VS}vacuum_error')"
    care_due = " or ".join(f"(states('{VS}{k}_time_left') | float(999)) < 24" for k, _ in CARE)
    hero = {
        "type": "custom:mushroom-template-card", "entity": VAC, "icon": "mdi:robot-vacuum", "multiline_secondary": True,
        "color": (f"{{% set s = states('{VAC}') %}}{{% if {err} not in ['none', 'unknown', 'unavailable'] or s == 'error' %}}var(--hp-bad)"
                  "{% elif s in ['cleaning', 'returning'] %}var(--hp-gold){% elif s == 'docked' %}var(--hp-ok){% else %}var(--hp-muted){% endif %}"),
        "badge_icon": (f"{{% if {err} not in ['none', 'unknown', 'unavailable'] %}}mdi:alert{{% elif {care_due} %}}mdi:wrench{{% endif %}}"),
        "badge_color": f"{{{{ 'var(--hp-bad)' if {err} not in ['none', 'unknown', 'unavailable'] else 'var(--hp-warn)' }}}}",
        "primary": (f"{{% if {err} not in ['none', 'unknown', 'unavailable'] %}}Needs help · {{{{ state_translated('{VS}vacuum_error') }}}}"
                    f"{{% elif is_state('{VAC}', 'docked') %}}Docked · {{{{ state_translated('{VS}status') }}}}"
                    f"{{% else %}}{{{{ state_translated('{VS}status') }}}}{{% endif %}}"),
        "secondary": (f"{{% set mins = states('{VS}cleaning_time') | float(0) %}}{{% set ft2 = (states('{VS}cleaning_area') | float(0) * 10.7639) | round(0) | int %}}"
                      f"{{% if is_state('binary_sensor.living_room_vacuum_cleaning', 'on') %}}"
                      f"{{{{ states('{VS}current_room') }}}} · {{{{ mins | round(0) | int }}}} min · {{{{ ft2 }}}} ft²"
                      f"{{% elif has_value('{VS}last_clean_end') %}}"
                      f"Last clean {{{{ relative_time(as_datetime(states('{VS}last_clean_end'))) }}}} ago · {{{{ mins | round(0) | int }}}} min · {{{{ ft2 }}}} ft²"
                      "{% endif %}"
                      + "".join(f"{{% set h = states('{VS}{k}_time_left') | float(999) %}}{{% if h < 24 %}}{{{{ '\\n' }}}}⚠ {n}: "
                                f"{{{{ 'due now' if h <= 0 else 'clean in ' ~ (h | round(0) | int) ~ ' h' }}}}{{% endif %}}" for k, n in CARE)),
        "tap_action": {"action": "more-info"}, "hold_action": {"action": "none"}}
    controls = [
        _chip("Clean", "mdi:play", _run("vacuum.start", confirm="Start cleaning the whole selected map?")),
        _chip("Pause", "mdi:pause", _run("vacuum.pause")),
        _chip("Dock", "mdi:home-import-outline", _run("vacuum.return_to_base")),
        _chip("Locate", "mdi:map-marker-outline", _run("vacuum.locate")),
    ]
    mode_sel = "select.living_room_vacuum_cleaning_mode"
    mop_sel = "select.living_room_vacuum_mop_intensity"
    settings = [
        _chip(f"{{{{ 'Vac & Mop' if is_state('{mode_sel}', 'vac_and_mop') else 'Vacuum' }}}}",
              f"{{{{ 'mdi:water' if is_state('{mode_sel}', 'vac_and_mop') else 'mdi:broom' }}}}",
              _run("select.select_next", {"cycle": True}, mode_sel), {"action": "more-info"}, entity=mode_sel),
        _chip(f"{{{{ state_attr('{VAC}', 'fan_speed') | default('Fan', true) | title }}}}", "mdi:fan",
              {"action": "more-info"}, entity=VAC),
        _when(_chip(f"Mop {{{{ state_translated('{mop_sel}') | lower }}}}", "mdi:water-percent", {"action": "more-info"}, entity=mop_sel),
              {"condition": "state", "entity": mode_sel, "state": "vac_and_mop"}),
        _chip("Full clean", "mdi:auto-fix", _run("button.press", target="button.living_room_vacuum_full_cleaning",
                                                 confirm="Run your Full cleaning routine now?")),
    ]
    map_sel = "select.living_room_vacuum_selected_map"
    rooms = [_chip(f"{{{{ states('{map_sel}') | replace('Homesite ', '') }}}}", "mdi:map-outline", {"action": "more-info"}, entity=map_sel)]
    for m, segs in ROOMS.items():
        for seg, name in segs:
            rooms.append(_when(_chip(name, "mdi:broom", _run("vacuum.send_command",
                                                                   {"command": "app_segment_clean", "params": [{"segments": [seg], "repeat": 1}]},
                                                                   confirm=f"Clean the {name} now?")),
                               {"condition": "state", "entity": map_sel, "state": m}))
    heading = {"type": "heading", "heading": "Cleaning", "heading_style": "title", "icon": "mdi:robot-vacuum", "badges": [
        {"type": "entity", "entity": f"{VS}battery", "show_state": True, "show_icon": True, "tap_action": {"action": "more-info"}}]}
    return {"type": "vertical-stack", "cards": [
        heading, hero,
        {"type": "grid", "columns": 2, "square": False, "cards": [
            _panel("Controls", controls, heading_mod, panel_mod), _panel("Settings", settings, heading_mod, panel_mod)]},
        _panel("Rooms", rooms, heading_mod, panel_mod),
        _dots("●  ○")]}


def _media_page(heading_mod, panel_mod):
    live = ["playing", "paused"]
    heroes = []
    for eid, name, kind in PLAYERS:
        heroes.append({
            "type": "custom:mushroom-template-card", "entity": eid, "icon": "mdi:play-circle", "color": "var(--hp-gold)",
            "picture": f"{{{{ state_attr('{eid}', 'entity_picture') or '' }}}}",
            "primary": f"{{{{ state_attr('{eid}', 'media_title') or state_attr('{eid}', 'app_name') or 'Playing' }}}}",
            "secondary": (f"{{% set by = state_attr('{eid}', 'media_artist') or state_attr('{eid}', 'app_name') %}}"
                          f"{{{{ 'Paused · ' if is_state('{eid}', 'paused') else '' }}}}{{{{ (by ~ ' · ') if by and by != state_attr('{eid}', 'media_title') else '' }}}}on {name}"),
            "tap_action": {"action": "more-info"}, "hold_action": {"action": "none"},
            "visibility": [{"condition": "state", "entity": eid, "state": live}]})
    heroes.append({
        "type": "custom:mushroom-template-card", "icon": "mdi:music-note-off", "color": "var(--hp-muted)",
        "primary": "Nothing playing", "secondary": "Tap a device below for its controls",
        "tap_action": {"action": "none"}, "hold_action": {"action": "none"},
        "visibility": [{"condition": "state", "entity": e, "state_not": live} for e, _, _ in PLAYERS]})

    def pill(eid, name, kind):
        on_icon, off_icon = ICONS[kind]
        playing = f"is_state('{eid}', 'playing')"
        if eid == "media_player.great_room_sony_tv":  # the Cast side of the same TV counts as playing too
            playing = f"(is_state('{eid}', 'playing') or is_state('media_player.sony_tv', 'playing'))"
        chip = _chip(name, f"{{{{ '{off_icon}' if is_state('{eid}', 'off') else '{on_icon}' }}}}", {"action": "more-info"},
                     _run("media_player.toggle", target=eid) if eid == "media_player.great_room_sony_tv" else {"action": "more-info"},
                     f"{{{{ 'yellow' if {playing} else ('grey' if is_state('{eid}', 'off') else '') }}}}", entity=eid)
        return _when(chip, {"condition": "state", "entity": eid, "state_not": "unavailable"})
    by_kind = lambda k: [pill(e, n, kd) for e, n, kd in PLAYERS if kd == k]  # noqa: E731
    heading = {"type": "heading", "heading": "Media", "heading_style": "title", "icon": "mdi:speaker-multiple"}
    return {"type": "vertical-stack", "cards": [
        heading, *heroes,
        {"type": "grid", "columns": 2, "square": False, "cards": [
            _panel("TVs", by_kind("tv"), heading_mod, panel_mod), _panel("Speakers", by_kind("speaker"), heading_mod, panel_mod),
            _panel("Displays", by_kind("display"), heading_mod, panel_mod), _panel("Groups", by_kind("group"), heading_mod, panel_mod)]},
        _dots("○  ●")]}


def step_cleaning_media(apply):
    """Areas for the media devices, then a Cleaning <-> Media swipe card replacing both sections."""
    ws = h.HAWebSocket()
    require_admin(ws)
    areas = {a["name"]: a["area_id"] for a in ws.call("config/area_registry/list")}
    reg = {e["entity_id"]: e for e in ws.call("config/entity_registry/list")}
    devs = {d["id"]: d for d in ws.call("config/device_registry/list")}
    dev_upd = {}
    for eid, area in MEDIA_AREAS.items():
        d = devs[reg[eid]["device_id"]]
        print(f"device {(d.get('name_by_user') or d.get('name'))!r:26} area {d.get('area_id')} -> {areas[area]}")
        dev_upd[d["id"]] = areas[area]
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    secs = dash["views"][0]["sections"]
    idx = {s["cards"][0].get("heading"): i for i, s in enumerate(secs) if s.get("cards")}
    swipes = [c for s in secs for c in s.get("cards", []) if c.get("type") == "horizontal-stack"]
    lights = [c for c in swipes if c["cards"][1]["cards"][0].get("heading") == "Lights & plugs"]
    if "Cleaning" not in idx or "Media" not in idx or len(lights) != 1:
        ws.close()
        sys.exit("Expected Cleaning and Media sections and the Garage/Lights swipe card; stopping.")
    sample = next(g for g in lights[0]["cards"][1]["cards"] if g.get("type") == "grid")["cards"][0]
    heading_mod, panel_mod = sample["cards"][0]["card_mod"], sample["card_mod"]
    swipe = {"type": "horizontal-stack", "cards": [_cleaning_page(heading_mod, panel_mod), _media_page(heading_mod, panel_mod)],
             "card_mod": lights[0]["card_mod"], "grid_options": {"columns": "full"}}
    print(f"Replace Cleaning section ({len(secs[idx['Cleaning']]['cards']) - 1} tiles) with Cleaning <-> Media swipe card")
    print(f"Remove Media section ({len(secs[idx['Media']]['cards']) - 1} tiles)")
    print("Cleaning page: heading+battery, status card, [Controls | Settings], Rooms (follows selected map), dots")
    print(f"Media page: now-playing card ({len(PLAYERS)} players) or 'Nothing playing', [TVs | Speakers], [Displays | Groups], dots")
    out = PAYLOADS / "cleaning_media_swipe.json"
    out.write_text(json.dumps(swipe, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"preview saved to {out.relative_to(ROOT)}")
    if apply:
        for did, aid in dev_upd.items():
            ws_raw(ws, "config/device_registry/update", device_id=did, area_id=aid)
        print("areas set")
        sec = secs[idx["Cleaning"]]
        secs[idx["Cleaning"]] = {**{k: v for k, v in sec.items() if k not in ("cards", "column_span")}, "cards": [swipe]}
        del secs[idx["Media"]]
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    ws.close()


def step_remove_office(apply):
    """Remove the 'Office · Epson' printer section from Home (it stays on the Office room page)."""
    ws = h.HAWebSocket()
    require_admin(ws)
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    secs = dash["views"][0]["sections"]
    hits = [i for i, s in enumerate(secs) if s.get("cards") and str(s["cards"][0].get("heading", "")).startswith("Office")]
    if len(hits) != 1:
        ws.close()
        sys.exit(f"Expected one Office section on Home, found {len(hits)}; stopping.")
    sec = secs[hits[0]]
    print(f"Remove section '{sec['cards'][0]['heading']}': {[c.get('entity') for c in sec['cards'][1:]]}")
    elsewhere = [v.get("path") for v in dash["views"][1:] if "sensor.epson_et_2850_series" in json.dumps(v)]
    print(f"Printer still shown on: {elsewhere}")
    if apply:
        del secs[hits[0]]
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    ws.close()


SHORTCUT_STYLE = (
    "ha-card {\n  border-radius: 999px !important;\n  padding-block: 14px 16px !important;\n"
    "  --tile-icon-size: 52px;\n  --mush-icon-size: 52px;\n}\n")


def _shortcut(label, icon, color, path, status):
    return {"type": "custom:mushroom-template-card", "vertical": True, "icon": icon, "color": color,
            "primary": label, "secondary": status,
            "tap_action": {"action": "navigate", "navigation_path": f"/{DASHBOARD}/{path}"},
            "hold_action": {"action": "none"}, "card_mod": {"style": SHORTCUT_STYLE}}


def step_shortcuts(apply):
    """Three pill shortcuts under the header chips, each opening its own page (Climate, Lights & plugs, Media)."""
    ws = h.HAWebSocket()
    require_admin(ws)
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    views = dash["views"]
    if any(v.get("path") in ("climate", "lights", "media") for v in views):
        ws.close()
        sys.exit("A climate/lights/media page already exists; stopping.")
    secs = views[0]["sections"]
    swipes = {c["cards"][1]["cards"][0].get("heading"): c for s in secs for c in s.get("cards", []) if c.get("type") == "horizontal-stack"}
    if set(swipes) != {"Climate", "Lights & plugs", "Media"}:
        ws.close()
        sys.exit(f"Unexpected swipe cards: {list(swipes)}; stopping.")
    old = json.loads((BACKUPS / "20260926-231154" / f"dashboard.{DASHBOARD}.json").read_text(encoding="utf-8"))
    climate_old = next(s for s in old["views"][0]["sections"] if s["cards"][0].get("heading") == "Climate")
    not_dots = lambda c: not (c.get("type") == "markdown" and "●" in str(c.get("content", "")))  # noqa: E731
    full = lambda c: {**c, "grid_options": {"columns": "full"}} if c.get("type") != "heading" else c  # noqa: E731

    def page(path, title, cards, span=2):
        return {"title": title, "path": path, "subview": True, "back_path": f"/{DASHBOARD}/home", "type": "sections",
                "max_columns": 2, "theme": "Home Panel", "sections": [{"type": "grid", "column_span": span, "cards": cards}]}
    pages = [
        page("climate", "Climate", climate_old["cards"]),
        page("lights", "Lights & plugs", [full(c) for c in swipes["Lights & plugs"]["cards"][1]["cards"] if not_dots(c)]),
        page("media", "Media", [full(c) for c in swipes["Media"]["cards"][1]["cards"] if not_dots(c)]),
    ]
    lights_hero = swipes["Lights & plugs"]["cards"][1]["cards"][1]
    playing = " + ".join(f"(1 if is_state('{e}', 'playing') else 0)" for e, _, _ in PLAYERS if e != "media_player.great_room_sony_tv")
    temps = "[state_attr('climate.great_room_thermostat', 'current_temperature'), state_attr('climate.living_room_thermostat', 'current_temperature')]"
    row = {"type": "grid", "columns": 3, "square": False, "grid_options": {"columns": "full"}, "cards": [
        _shortcut("Climate", "mdi:thermostat", "var(--hp-sky)", "climate",
                  f"{{% set t = {temps} | select('is_number') | list %}}{{{{ ((t | sum / t | count) | round(0) | int ~ '° inside') if t else '—' }}}}"),
        _shortcut("Lights", "mdi:lightbulb-group", "var(--hp-gold)", "lights", lights_hero["primary"]),
        _shortcut("Media", "mdi:play-circle", "var(--purple-color)", "media",
                  f"{{% set n = {playing} %}}{{{{ (n ~ ' playing') if n else 'Nothing on' }}}}"),
    ]}
    head = secs[0]["cards"]
    at = next(i for i, c in enumerate(head) if c.get("type") == "clock") + 1  # right below the time and date
    print(f"Header: insert shortcut row right below the clock/date, above the chips (position {at}): Climate · Lights · Media")
    for p in pages:
        print(f"New page /{DASHBOARD}/{p['path']} '{p['title']}' ({len(p['sections'][0]['cards'])} cards, back arrow to Home)")
    show("shortcut (Climate)", row["cards"][0])
    out = PAYLOADS / "shortcuts.json"
    out.write_text(json.dumps({"row": row, "pages": pages}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"preview saved to {out.relative_to(ROOT)}")
    if apply:
        head.insert(at, row)
        views.extend(pages)
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    ws.close()


def _pill_style(color):
    # capsule with a faint tint of the button's colour; ha-tile-icon sets --tile-icon-size on itself, so target it directly
    return (
        "ha-card {\n  border-radius: 999px !important;\n  padding-block: 16px 18px !important;\n"
        f"  background: linear-gradient(180deg, color-mix(in srgb, {color} 16%, transparent), transparent 70%), "
        "var(--ha-card-background, var(--card-background-color)) !important;\n"
        f"  border: 1px solid color-mix(in srgb, {color} 30%, transparent) !important;\n}}\n"
        "ha-tile-icon {\n  --tile-icon-size: 52px;\n  --mdc-icon-size: 28px;\n  --tile-icon-opacity: 0.22;\n}\n")


PILL_ROW_STYLE = (  # three fixed-width capsules, centred, like the reference
    ":host #root {\n  grid-template-columns: repeat(3, minmax(0, 108px)) !important;\n"
    "  justify-content: center;\n  gap: 14px !important;\n}\n")
CLOCK_STYLE = {"hui-clock-card-digital$": (
    ":host {\n  display: flex !important;\n  align-items: flex-end;\n  justify-content: space-between;\n  gap: 12px;\n"
    "  padding: 0 4px;\n  box-sizing: border-box;\n}\n"
    ".clock-container {\n  width: auto;\n  justify-content: flex-start;\n}\n"
    ".date-container {\n  width: auto;\n  margin-top: 0;\n  padding-bottom: 2px;\n}\n"
    ".date {\n  width: auto;\n  text-align: right;\n  opacity: 1;\n}\n"
    ".date-line:first-child {\n  font-size: 12px;\n  letter-spacing: 0.12em;\n  text-transform: uppercase;\n  opacity: 0.65;\n}\n"
    ".date-line:last-child {\n  font-size: 19px;\n  opacity: 0.9;\n}\n")}


def step_header_polish(apply):
    """Clock: smaller, time left + date right. Shortcuts: narrow capsules with tinted icon circles."""
    ws = h.HAWebSocket()
    require_admin(ws)
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    head = dash["views"][0]["sections"][0]["cards"]
    clock = next(c for c in head if c.get("type") == "clock")
    row = next((c for c in head if c.get("type") == "grid" and all(
        x.get("tap_action", {}).get("action") == "navigate" for x in c.get("cards", []))), None)
    if not row:
        ws.close()
        sys.exit("Shortcut row not found; stopping.")
    new_clock = {**clock, "clock_size": "medium",
                 "date_format": ["weekday-long", "separator-new-line", "month-long", "day-numeric"],
                 "card_mod": {"style": CLOCK_STYLE}}
    print(f"Clock: size {clock.get('clock_size')} -> medium; date '{' '.join(clock['date_format'])}' -> 'Sunday' / 'September 27' on the right")
    new_cards = []
    for c in row["cards"]:
        n = {**c, "icon_tap_action": c["tap_action"], "card_mod": {"style": _pill_style(c["color"])}}
        new_cards.append(n)
        print(f"  {c['primary']:8} capsule 108px, tinted {c['color']}, 52px icon circle (icon also opens the page)")
    show("clock card_mod", new_clock["card_mod"])
    if apply:
        head[head.index(clock)] = new_clock
        row["cards"] = new_cards
        row["card_mod"] = {"style": PILL_ROW_STYLE}
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    ws.close()


CLIMATE_ROOMS = [  # label, temperature sensor, humidity sensor, occupancy sensor
    ("Great Room", "sensor.great_room_thermostat_temperature", "sensor.great_room_thermostat_humidity", None),
    ("Living Room", "sensor.living_room_thermostat_temperature", "sensor.living_room_thermostat_humidity",
     "binary_sensor.living_room_thermostat_occupancy"),
    ("Bedroom", "sensor.bedroom_sensor_temperature", None, "binary_sensor.bedroom_sensor_occupancy"),
]
DIALS = [("climate.great_room_thermostat", "Great Room", "heat"), ("climate.living_room_thermostat", "Living Room", "cool")]


def _climate_summary():
    gr, lr = DIALS[0][0], DIALS[1][0]
    temps = f"[state_attr('{gr}', 'current_temperature'), state_attr('{lr}', 'current_temperature')]"
    status = lambda e, word, act, mode: (  # noqa: E731
        f"{{{{ '{act}' if state_attr('{e}', 'hvac_action') == '{act.lower()}' else ('{word} on' if is_state('{e}', '{mode}') "
        f"else ('{word} unavailable' if is_state('{e}', 'unavailable') else '{word} off')) }}}}")
    return {
        "type": "custom:mushroom-template-card", "icon": "mdi:home-thermometer", "grid_options": {"columns": "full"},
        "color": (f"{{% if state_attr('{gr}', 'hvac_action') == 'heating' %}}var(--hp-warn)"
                  f"{{% elif state_attr('{lr}', 'hvac_action') == 'cooling' %}}var(--hp-sky){{% else %}}var(--hp-muted){{% endif %}}"),
        "primary": (f"{{% set t = {temps} | select('is_number') | list %}}{{% set o = state_attr('weather.forecast_home', 'temperature') %}}"
                    "{{ ((t | sum / t | count) | round(0) | int ~ '° inside') if t else 'Inside —' }}"
                    "{% if is_number(o) %} · {{ o | round(0) | int }}° outside{% endif %}"),
        "secondary": status(gr, "Heat", "Heating", "heat") + " · " + status(lr, "Cooling", "Cooling", "cool"),
        "tap_action": {"action": "none"}, "hold_action": {"action": "none"}}


def _dial(eid, name, mode):
    return {"type": "thermostat", "entity": eid, "name": name, "grid_options": {"columns": "full"}, "features": [
        {"type": "climate-hvac-modes", "hvac_modes": [mode, "off"]},
        {"type": "climate-preset-modes", "style": "icons", "preset_modes": ["home", "away", "sleep"]}]}


def _room_panel(label, temp, hum, occ, heading_mod, panel_mod):
    chips = [_chip(f"{{{{ states('{temp}') | round(1) }}}}°", "mdi:thermometer", {"action": "more-info"}, entity=temp)]
    if hum:
        chips.append(_chip(f"{{{{ states('{hum}') | int(0) }}}}%", "mdi:water-percent", {"action": "more-info"},
                           icon_color=f"{{{{ 'amber' if states('{hum}') | int(0) > 60 else '' }}}}", entity=hum))
    if occ:
        chips.append(_when(_chip("Occupied", "mdi:account", {"action": "more-info"}, entity=occ),
                           {"condition": "state", "entity": occ, "state": "on"}))
    return _panel(label, chips, heading_mod, panel_mod)


def step_climate_page(apply):
    """Rebuild the Climate page: summary, two stacked thermostat dials, glass room panels (Lights-page style)."""
    ws = h.HAWebSocket()
    require_admin(ws)
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    views = [v for v in dash["views"] if v.get("path") == "climate"]
    lights = [c for s in dash["views"][0]["sections"] for c in s.get("cards", [])
              if c.get("type") == "horizontal-stack" and c["cards"][1]["cards"][0].get("heading") == "Lights & plugs"]
    if len(views) != 1 or len(lights) != 1:
        ws.close()
        sys.exit("Climate page or Lights swipe card not found; stopping.")
    sample = next(g for g in lights[0]["cards"][1]["cards"] if g.get("type") == "grid")["cards"][0]
    heading_mod, panel_mod = sample["cards"][0]["card_mod"], sample["card_mod"]
    outside = _panel("Outside", [{"type": "weather", "entity": "weather.forecast_home", "show_temperature": True,
                                  "show_conditions": True, "tap_action": {"action": "more-info"}}], heading_mod, panel_mod)
    panels = [_room_panel(*r, heading_mod, panel_mod) for r in CLIMATE_ROOMS] + [outside]
    sections = [
        {"type": "grid", "column_span": 2, "cards": [_climate_summary()]},
        {"type": "grid", "cards": [_dial(*DIALS[0])]},
        {"type": "grid", "cards": [_dial(*DIALS[1])]},
        {"type": "grid", "column_span": 2, "cards": [
            {"type": "grid", "columns": 2, "square": False, "grid_options": {"columns": "full"}, "cards": panels}]},
    ]
    old = views[0]["sections"]
    print(f"Climate page: {sum(len(s['cards']) for s in old)} cards -> summary, 2 dials (stacked on phone, side by side on tablet), 4 room panels")
    print("  dials: " + ", ".join(f"{n} ({m}/off, home/away/sleep)" for _, n, m in DIALS))
    print("  panels: " + ", ".join(r[0] for r in CLIMATE_ROOMS) + ", Outside")
    show("summary card", _climate_summary())
    if apply:
        views[0]["sections"] = sections
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    else:
        (PAYLOADS / "climate_page.json").write_text(json.dumps(sections, indent=2, ensure_ascii=False), encoding="utf-8")
        print("preview saved to ha-dashboard/outfit/payloads/climate_page.json")
    ws.close()


DIAL_STYLE = (  # theme colours for the dial arc and the selected mode/preset buttons; smaller dial, centred
    "ha-card {\n  --state-climate-heat-color: var(--hp-warn);\n  --state-climate-cool-color: var(--hp-sky);\n"
    "  --state-climate-off-color: color-mix(in srgb, var(--hp-ink) 20%, transparent);\n}\n"
    "ha-state-control-climate-temperature {\n  max-width: 250px;\n  margin: 0 auto;\n}\n")


def step_climate_polish(apply):
    """Climate page: themed selected buttons and dial colours, smaller dials, icon-only occupancy pills."""
    ws = h.HAWebSocket()
    require_admin(ws)
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    view = next((v for v in dash["views"] if v.get("path") == "climate"), None)
    if not view:
        ws.close()
        sys.exit("Climate page not found; stopping.")
    dials = [c for s in view["sections"] for c in s["cards"] if c.get("type") == "thermostat"]
    occ = [ch["chip"] for s in view["sections"] for g in s["cards"] if g.get("type") == "grid"
           for p in g["cards"] for c in p.get("cards", []) if c.get("type") == "custom:mushroom-chips-card"
           for ch in c["chips"] if ch.get("type") == "conditional" and ch["chip"].get("icon") == "mdi:account"]
    print(f"{len(dials)} dials: selected buttons soft glass instead of grey; heat amber, cool sky; dial max 250px wide")
    print(f"{len(occ)} occupancy pills: 'Occupied' text -> person icon only (tap still shows history)")
    if apply:
        for d in dials:
            d["card_mod"] = {"style": DIAL_STYLE}
        for ch in occ:
            ch["content"] = ""
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    ws.close()


def _dial_style(color):
    # same capsule language as the header shortcuts: faint wash + border in the system's colour
    return (
        "ha-card {\n  --state-climate-heat-color: var(--hp-warn);\n  --state-climate-cool-color: var(--hp-sky);\n"
        "  --state-climate-off-color: color-mix(in srgb, var(--hp-ink) 20%, transparent);\n"
        f"  background: linear-gradient(180deg, color-mix(in srgb, {color} 14%, transparent), transparent 60%), "
        "var(--ha-card-background, var(--card-background-color)) !important;\n"
        f"  border: 1px solid color-mix(in srgb, {color} 30%, transparent) !important;\n}}\n")


def step_climate_compact(apply):
    """Climate page on one phone screen: summary, both dials side by side (tinted), room panels."""
    ws = h.HAWebSocket()
    require_admin(ws)
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    view = next((v for v in dash["views"] if v.get("path") == "climate"), None)
    if not view:
        ws.close()
        sys.exit("Climate page not found; stopping.")
    cards = [c for s in view["sections"] for c in s["cards"]]
    summary = next(c for c in cards if c.get("icon") == "mdi:home-thermometer")
    dials = [c for c in cards if c.get("type") == "thermostat"]
    panels = next(c for c in cards if c.get("type") == "grid")
    tint = {"climate.great_room_thermostat": "var(--hp-warn)", "climate.living_room_thermostat": "var(--hp-sky)"}
    new_dials = [{**d, "grid_options": {"columns": 6, "rows": "auto"}, "card_mod": {"style": _dial_style(tint[d["entity"]])}}
                 for d in dials]
    print(f"Climate page: {len(view['sections'])} sections -> 1 section: summary, {len(new_dials)} dials side by side, room panels")
    for d in new_dials:
        print(f"  {d['name']}: half width, tinted {tint[d['entity']]}")
    if apply:
        view["sections"] = [{"type": "grid", "column_span": 2, "cards": [summary, *new_dials, panels]}]
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    ws.close()


def step_occupancy_pills(apply):
    """Climate page: occupancy pills always visible; dim outline when clear, champagne person when occupied."""
    ws = h.HAWebSocket()
    require_admin(ws)
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    view = next(v for v in dash["views"] if v.get("path") == "climate")
    changed = 0
    for s in view["sections"]:
        for g in s["cards"]:
            if g.get("type") != "grid":
                continue
            for p in g["cards"]:
                for c in p.get("cards", []):
                    if c.get("type") != "custom:mushroom-chips-card":
                        continue
                    for i, ch in enumerate(c["chips"]):
                        if ch.get("type") == "conditional" and ch["chip"].get("icon") == "mdi:account":
                            occ = ch["chip"]["entity"]
                            c["chips"][i] = _chip("", f"{{{{ 'mdi:account' if is_state('{occ}', 'on') else 'mdi:account-outline' }}}}",
                                                  {"action": "more-info"},
                                                  icon_color=f"{{{{ 'yellow' if is_state('{occ}', 'on') else 'grey' }}}}", entity=occ)
                            print(f"  {p['cards'][0]['heading']}: {occ} -> always shown, grey outline when clear, gold when occupied")
                            changed += 1
    if not changed:
        ws.close()
        sys.exit("No hidden occupancy pills found; nothing to do.")
    if apply:
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    ws.close()


def _tile_style(radius):
    return (f"ha-card {{\n  border-radius: {radius} !important;\n  padding-block: 12px 12px !important;\n}}\n"
            "ha-tile-icon {\n  --tile-icon-size: 44px;\n  --mdc-icon-size: 24px;\n}\n")


def _action_button(label, icon, color, tap):
    return {"type": "custom:mushroom-template-card", "vertical": True, "icon": icon, "color": color, "primary": label,
            "tap_action": tap, "icon_tap_action": tap, "hold_action": {"action": "none"},
            "card_mod": {"style": _tile_style("24px")}}


def _setting_tile(value, label, icon, color, entity):
    return {"type": "custom:mushroom-template-card", "vertical": True, "entity": entity, "icon": icon, "color": color,
            "primary": value, "secondary": label, "tap_action": {"action": "more-info"},
            "icon_tap_action": {"action": "more-info"}, "hold_action": {"action": "none"},
            "card_mod": {"style": _tile_style("20px")}}


def _rooms_panel(map_name, label, heading_mod, panel_mod):
    sel = "select.living_room_vacuum_selected_map"
    here = {"condition": "state", "entity": sel, "state": map_name}
    other = {"condition": "state", "entity": sel, "state_not": map_name}
    chips = [
        _when(_chip("Current map", "mdi:map-check-outline", {"action": "none"}, icon_color="yellow"), here),
        _when(_chip("Switch to this map", "mdi:map-outline",
                    {**_run("select.select_option", {"option": map_name}, sel),
                     "confirmation": {"text": f"Switch the vacuum to the {label} map? Only do this when it is on that floor."}},
                    icon_color="grey"), other)]
    for seg, name in ROOMS[map_name]:
        clean = _run("vacuum.send_command", {"command": "app_segment_clean", "params": [{"segments": [seg], "repeat": 1}]},
                     confirm=f"Clean the {name} now?")
        # same pill either way: cleans when this map is loaded, dimmed and inert when it isn't (IDs clash across maps)
        chips.append(_when(_chip(name, "mdi:broom", clean), here))
        chips.append(_when(_chip(name, "mdi:alert-outline", {"action": "none"}, icon_color="grey"), other))
    return _panel(label, chips, heading_mod, panel_mod)


def step_cleaning_rooms(apply):
    """Rebuild only the Upstairs/Downstairs room panels (room list from ROOMS; caution icon when the map isn't loaded)."""
    ws = h.HAWebSocket()
    require_admin(ws)
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    page = next(c["cards"][0] for s in dash["views"][0]["sections"] for c in s.get("cards", [])
                if c.get("type") == "horizontal-stack" and c["cards"][0]["cards"][0].get("heading") == "Cleaning")
    idx = {p["cards"][0].get("heading"): i for i, p in enumerate(page["cards"])
           if p.get("type") == "vertical-stack" and p.get("cards")}
    if "Upstairs" not in idx or "Downstairs" not in idx:
        ws.close()
        sys.exit("Room panels not found on the Cleaning page; stopping.")
    ref = page["cards"][idx["Upstairs"]]
    heading_mod, panel_mod = ref["cards"][0]["card_mod"], ref["card_mod"]
    for m, label in (("Homesite Upstairs", "Upstairs"), ("Homesite Downstairs", "Downstairs")):
        print(f"{label}: {', '.join(f'{n} ({s})' for s, n in ROOMS[m])}; unavailable rooms show a grey caution icon")
    if apply:
        page["cards"][idx["Upstairs"]] = _rooms_panel("Homesite Upstairs", "Upstairs", heading_mod, panel_mod)
        page["cards"][idx["Downstairs"]] = _rooms_panel("Homesite Downstairs", "Downstairs", heading_mod, panel_mod)
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    ws.close()


def _cleaning_page_v2(hero, heading, heading_mod, panel_mod):
    s = f"states('{VAC}')"
    mode_sel, mop_sel = "select.living_room_vacuum_cleaning_mode", "select.living_room_vacuum_mop_intensity"
    actions = {"type": "grid", "columns": 4, "square": False, "cards": [
        _action_button("Clean", "mdi:play", "var(--hp-gold)", _run("vacuum.start", confirm="Start cleaning the whole loaded map?")),
        _action_button("Pause", "mdi:pause", f"{{{{ 'var(--hp-gold)' if {s} == 'cleaning' else 'var(--hp-muted)' }}}}", _run("vacuum.pause")),
        _action_button("Dock", "mdi:home-import-outline", f"{{{{ 'var(--hp-muted)' if {s} == 'docked' else 'var(--hp-ok)' }}}}",
                       _run("vacuum.return_to_base")),
        _action_button("Routine", "mdi:auto-fix", "var(--hp-sky)",
                       _run("button.press", target="button.living_room_vacuum_full_cleaning", confirm="Run your Full cleaning routine now?")),
    ]}
    settings = {"type": "grid", "columns": 3, "square": False, "cards": [
        _setting_tile(f"{{{{ 'Vac & Mop' if is_state('{mode_sel}', 'vac_and_mop') else 'Vacuum' }}}}", "Mode",
                      f"{{{{ 'mdi:water' if is_state('{mode_sel}', 'vac_and_mop') else 'mdi:broom' }}}}", "var(--hp-sky)", mode_sel),
        _setting_tile(f"{{{{ state_attr('{VAC}', 'fan_speed') | default('—', true) | title }}}}", "Suction", "mdi:fan",
                      "var(--hp-sky)", VAC),
        _setting_tile(f"{{{{ (state_translated('{mop_sel}') | title) if is_state('{mode_sel}', 'vac_and_mop') else 'Off' }}}}", "Mop",
                      "mdi:water-percent",
                      f"{{{{ 'var(--hp-sky)' if is_state('{mode_sel}', 'vac_and_mop') else 'var(--hp-muted)' }}}}", mop_sel),
    ]}
    return {"type": "vertical-stack", "cards": [
        heading, hero, actions, settings,
        _rooms_panel("Homesite Upstairs", "Upstairs", heading_mod, panel_mod),
        _rooms_panel("Homesite Downstairs", "Downstairs", heading_mod, panel_mod),
        _dots("●  ○")]}


def step_cleaning_premium(apply):
    """Cleaning page: fixed action buttons, setting tiles (no tap-to-cycle), both maps' rooms always visible."""
    ws = h.HAWebSocket()
    require_admin(ws)
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    swipes = [c for s in dash["views"][0]["sections"] for c in s.get("cards", []) if c.get("type") == "horizontal-stack"]
    cm = [c for c in swipes if c["cards"][0]["cards"][0].get("heading") == "Cleaning"]
    if len(cm) != 1:
        ws.close()
        sys.exit("Cleaning/Media swipe card not found; stopping.")
    old = cm[0]["cards"][0]["cards"]
    heading, hero = old[0], old[1]
    sample = next(c for c in old if c.get("type") == "grid")["cards"][0]
    heading_mod, panel_mod = sample["cards"][0]["card_mod"], sample["card_mod"]
    hero = {**hero, "tap_action": {"action": "more-info"}}  # vacuum dialog also has Locate
    page = _cleaning_page_v2(hero, heading, heading_mod, panel_mod)
    print("Cleaning page: heading + status card kept")
    print("  actions (4 equal buttons): Clean (gold, confirm) · Pause (gold while cleaning) · Dock (sage when away) · Routine (sky, confirm)")
    print("  settings (3 tiles, tap opens picker): Mode · Suction · Mop (shows 'Off' in vacuum-only mode, never hidden)")
    print("  rooms: Upstairs panel (7) + Downstairs panel (1), always visible; other map's rooms dimmed and inert,")
    print("         'Current map' (gold) or 'Switch to this map' (confirm) at the start of each panel")
    print("  Locate moves into the status card's tap dialog")
    (PAYLOADS / "cleaning_page.json").write_text(json.dumps(page, indent=2, ensure_ascii=False), encoding="utf-8")
    if apply:
        cm[0]["cards"][0] = page
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    ws.close()


MAP_IMAGES = {"Homesite Upstairs": "image.living_room_vacuum_homesite_upstairs",
              "Homesite Downstairs": "image.living_room_vacuum_homesite_downstairs"}
PART_LIFE = [("main_brush", "Main brush", 300), ("side_brush", "Side brush", 200), ("filter", "Filter", 150),
             ("sensor", "Sensors", 30)]  # Roborock's standard lifespans in hours (HA only reports hours left)
DECK_BUTTON = ("ha-card {\n  background: color-mix(in srgb, var(--hp-ink) 6%, transparent) !important;\n"
               "  border: none !important;\n  box-shadow: none !important;\n  border-radius: 18px !important;\n"
               "  padding-block: 10px !important;\n}\nha-tile-icon {\n  --tile-icon-size: 40px;\n  --mdc-icon-size: 22px;\n}\n")


def _map_card(map_name, height):
    sel = "select.living_room_vacuum_selected_map"
    return {"type": "picture", "image_entity": MAP_IMAGES[map_name], "tap_action": {"action": "more-info"},
            "hold_action": {"action": "none"},
            "visibility": [{"condition": "state", "entity": sel, "state": map_name}] if height else [],
            "card_mod": {"style": {
                ".": "ha-card {\n  overflow: hidden;\n  padding: 8px;\n  box-sizing: border-box;\n}\n",
                "hui-image$": f"img {{\n  width: 100%;\n  max-height: {height or 420}px;\n  object-fit: contain;\n}}\n"}}}


def _deck(panel_mod):
    s = f"states('{VAC}')"
    mode_sel, mop_sel = "select.living_room_vacuum_cleaning_mode", "select.living_room_vacuum_mop_intensity"
    btn = lambda label, icon, color, tap: {**_action_button(label, icon, color, tap), "card_mod": {"style": DECK_BUTTON}}  # noqa: E731
    buttons = {"type": "grid", "columns": 4, "square": False, "cards": [
        btn("Clean", "mdi:play", "var(--hp-gold)", _run("vacuum.start", confirm="Start cleaning the whole loaded map?")),
        btn("Pause", "mdi:pause", f"{{{{ 'var(--hp-gold)' if {s} == 'cleaning' else 'var(--hp-muted)' }}}}", _run("vacuum.pause")),
        btn("Dock", "mdi:home-import-outline", f"{{{{ 'var(--hp-muted)' if {s} == 'docked' else 'var(--hp-ok)' }}}}",
            _run("vacuum.return_to_base")),
        btn("Routine", "mdi:auto-fix", "var(--hp-sky)",
            _run("button.press", target="button.living_room_vacuum_full_cleaning", confirm="Run your Full cleaning routine now?")),
    ]}
    settings = {"type": "custom:mushroom-chips-card", "alignment": "center", "chips": [
        _chip(f"{{{{ 'Vac & Mop' if is_state('{mode_sel}', 'vac_and_mop') else 'Vacuum' }}}}",
              f"{{{{ 'mdi:water' if is_state('{mode_sel}', 'vac_and_mop') else 'mdi:broom' }}}}", {"action": "more-info"}, entity=mode_sel),
        _chip(f"{{{{ state_attr('{VAC}', 'fan_speed') | default('—', true) | title }}}}", "mdi:fan", {"action": "more-info"}, entity=VAC),
        _chip(f"Mop {{{{ (state_translated('{mop_sel}') | lower) if is_state('{mode_sel}', 'vac_and_mop') else 'off' }}}}",
              "mdi:water-percent", {"action": "more-info"},
              icon_color=f"{{{{ '' if is_state('{mode_sel}', 'vac_and_mop') else 'grey' }}}}", entity=mop_sel),
    ]}
    style = panel_mod["style"].replace("--vertical-stack-card-gap: 0px;", "--vertical-stack-card-gap: 12px;")
    return {"type": "vertical-stack", "card_mod": {"style": style}, "cards": [buttons, settings]}


def _vac_badges(battery_badge):
    b = "binary_sensor.living_room_vacuum_"
    pair = lambda ent, icon, name: [  # noqa: E731
        {"type": "entity", "entity": ent, "icon": icon, "name": name, "show_state": False, "show_icon": True,
         "color": "var(--hp-ok)", "tap_action": {"action": "more-info"},
         "visibility": [{"condition": "state", "entity": ent, "state": "on"}]},
        {"type": "entity", "entity": ent, "icon": "mdi:alert-circle-outline", "name": f"{name} missing", "show_state": False,
         "show_icon": True, "color": "var(--hp-bad)", "tap_action": {"action": "more-info"},
         "visibility": [{"condition": "state", "entity": ent, "state_not": "on"}]}]
    return [*pair(f"{b}mop_attached", "mdi:water-outline", "Mop pad"), *pair(f"{b}water_box_attached", "mdi:cup-water", "Water tank"),
            {"type": "entity", "entity": f"{b}water_shortage", "icon": "mdi:water-alert", "show_state": False, "show_icon": True,
             "color": "var(--hp-bad)", "tap_action": {"action": "more-info"},
             "visibility": [{"condition": "state", "entity": f"{b}water_shortage", "state": "on"}]},
            {"type": "entity", "entity": "switch.living_room_vacuum_do_not_disturb", "name": "Do Not Disturb",
             "icon": "mdi:weather-night", "show_state": False, "show_icon": True, "color": "var(--hp-gold)",
             "tap_action": {"action": "toggle"}, "hold_action": {"action": "more-info"}},  # gold when on, dim when off
            battery_badge]


def _care_card(key, name, life):
    e = f"{VS}{key}_time_left"
    pct = f"((states('{e}') | float(0)) / {life} * 100) | round(0) | int"
    return {"type": "custom:mushroom-template-card", "entity": e, "icon": "mdi:wrench-clock", "primary": name,
            "secondary": f"{{% set h = states('{e}') | float(0) %}}{{% set p = {pct} %}}"
                         "{{ 'Due now' if h <= 0 else (h | round(0) | int) ~ ' h left · ' ~ [p, 100] | min ~ '%' }}",
            "color": f"{{% set p = {pct} %}}{{% if p <= 5 %}}var(--hp-bad){{% elif p <= 20 %}}var(--hp-warn){{% else %}}var(--hp-sky){{% endif %}}",
            "features": [{"type": "bar-gauge", "min": 0, "max": life}], "features_position": "bottom",
            "tap_action": {"action": "more-info"}, "hold_action": {"action": "none"}, "grid_options": {"columns": 6}}


def _care_link():
    lowest = " , ".join(f"('{n}', states('{VS}{k}_time_left') | float(999), {life})" for k, n, life in PART_LIFE)
    return {"type": "custom:mushroom-template-card", "icon": "mdi:wrench-clock", "color": "var(--hp-muted)",
            "primary": "Care & history",
            "secondary": (f"{{% set parts = [{lowest}] | sort(attribute=1) %}}{{% set p = parts[0] %}}"
                          "Next: {{ p[0] }} in {{ p[1] | round(0) | int }} h"),
            "badge_icon": "mdi:chevron-right",
            "tap_action": {"action": "navigate", "navigation_path": f"/{DASHBOARD}/cleaning"},
            "icon_tap_action": {"action": "navigate", "navigation_path": f"/{DASHBOARD}/cleaning"},
            "hold_action": {"action": "none"}}


def _cleaning_subview(heading_mod, panel_mod):
    stats = {"type": "custom:mushroom-template-card", "icon": "mdi:chart-timeline-variant", "color": "var(--hp-gold)",
             "primary": f"{{{{ states('{VS}total_cleaning_count') }}}} cleans · {{{{ states('{VS}total_cleaning_time') | float(0) | round(0) | int }}}} h",
             "secondary": (f"{{{{ ((states('{VS}total_cleaning_area') | float(0) * 10.7639) / 1000) | round(1) }}}}k ft² lifetime"
                           f"{{% if has_value('{VS}last_clean_begin') %}} · last run {{{{ as_timestamp(states('{VS}last_clean_begin')) | timestamp_custom('%b %-d, %-I:%M %p') }}}}{{% endif %}}"),
             "tap_action": {"action": "none"}, "hold_action": {"action": "none"}, "grid_options": {"columns": "full"}}
    return {"title": "Cleaning", "path": "cleaning", "subview": True, "back_path": f"/{DASHBOARD}/home", "type": "sections",
            "max_columns": 2, "theme": "Home Panel", "sections": [
                {"type": "grid", "column_span": 2, "cards": [
                    {"type": "heading", "heading": "Care", "heading_style": "title", "icon": "mdi:wrench-clock"},
                    *[_care_card(*p) for p in PART_LIFE],
                    {"type": "heading", "heading": "History", "heading_style": "title", "icon": "mdi:history"},
                    stats,
                    {"type": "heading", "heading": "Maps", "heading_style": "title", "icon": "mdi:map-outline"},
                    {**_map_card("Homesite Upstairs", 0), "grid_options": {"columns": 6}},
                    {**_map_card("Homesite Downstairs", 0), "grid_options": {"columns": 6}},
                ]}]}


def step_cleaning_deck(apply):
    """Home Cleaning page: live map, status, control deck, rooms, Care link. New /cleaning page: care bars, stats, maps."""
    ws = h.HAWebSocket()
    require_admin(ws)
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    if any(v.get("path") == "cleaning" for v in dash["views"]):
        ws.close()
        sys.exit("A /cleaning page already exists; stopping.")
    swipe = next(c for s in dash["views"][0]["sections"] for c in s.get("cards", [])
                 if c.get("type") == "horizontal-stack" and c["cards"][0]["cards"][0].get("heading") == "Cleaning")
    page = swipe["cards"][0]["cards"]
    heading, hero = page[0], page[1]
    rooms = [p for p in page if p.get("type") == "vertical-stack" and p["cards"][0].get("heading") in ("Upstairs", "Downstairs")]
    if len(rooms) != 2:
        ws.close()
        sys.exit("Room panels not found; stopping.")
    heading_mod, panel_mod = rooms[0]["cards"][0]["card_mod"], rooms[0]["card_mod"]
    new_heading = {**heading, "badges": _vac_badges(heading["badges"][0])}
    new_page = {"type": "vertical-stack", "cards": [
        new_heading, _map_card("Homesite Upstairs", 220), _map_card("Homesite Downstairs", 220), hero, _deck(panel_mod),
        *rooms, _care_link(), _dots("●  ○")]}
    sub = _cleaning_subview(heading_mod, panel_mod)
    print("Home > Cleaning page:")
    print("  header badges: mop pad + water tank (sage, or rose alert if missing), water shortage (only if low), battery")
    print("  live map of the loaded floor (max 220px tall, tap for full view), status card")
    print("  control deck: Clean · Pause · Dock · Routine + Mode / Suction / Mop pills (replaces the two tile rows)")
    print("  Upstairs + Downstairs rooms (unchanged), 'Care & history' link showing the next part due")
    print("New page /home-panel/cleaning: 4 care bars (" + ", ".join(f"{n} {l} h" for _, n, l in PART_LIFE) +
          "), lifetime stats, both floor maps")
    (PAYLOADS / "cleaning_deck.json").write_text(json.dumps({"page": new_page, "subview": sub}, indent=2, ensure_ascii=False),
                                                 encoding="utf-8")
    if apply:
        swipe["cards"][0] = new_page
        dash["views"].append(sub)
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    ws.close()


MAP_CROP = (  # picture card renders a plain <img>; Roborock's canvas has wide empty margins, so zoom into the middle
    "ha-card {\n  overflow: hidden;\n  padding: 0 !important;\n  height: 230px;\n  display: flex;\n"
    "  align-items: center;\n  justify-content: center;\n}\n"
    "img {\n  height: 230px;\n  width: 100%;\n  object-fit: contain;\n  transform: scale(1.55);\n}\n")


def step_map_crop(apply):
    """Home Cleaning page: fixed 230px map card, image zoomed 1.55x so the floor plan fills it."""
    ws = h.HAWebSocket()
    require_admin(ws)
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    page = next(c["cards"][0] for s in dash["views"][0]["sections"] for c in s.get("cards", [])
                if c.get("type") == "horizontal-stack" and c["cards"][0]["cards"][0].get("heading") == "Cleaning")
    maps = [c for c in page["cards"] if c.get("type") == "picture" and c.get("image_entity") in MAP_IMAGES.values()]
    if len(maps) != 2:
        ws.close()
        sys.exit(f"Expected 2 map cards on the Cleaning page, found {len(maps)}; stopping.")
    for m in maps:
        print(f"{m['image_entity']}: height ~330px -> 230px, zoom 1.55x (empty canvas clipped)")
    if apply:
        for m in maps:
            m["card_mod"] = {"style": MAP_CROP}
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    ws.close()


ALL_FOLDERS = ["share", "addons/local", "ssl", "media"]  # Supervisor folder IDs besides the Home Assistant config


def step_backup_folders(apply):
    """B2: include every folder in the automatic backup (only include_folders changes; nothing else is touched)."""
    ws = h.HAWebSocket()
    require_admin(ws)
    cfg = ws_raw(ws, "backup/config/info")["config"]
    cb = cfg.get("create_backup", {})
    current = sorted(cb.get("include_folders") or [])
    print(f"automatic backup folders: {current or 'none selected'} -> {sorted(ALL_FOLDERS)}")
    print("unchanged: locations " + str(cb.get("agent_ids")) + f", database {cb.get('include_database')}, all add-ons "
          f"{cb.get('include_all_addons')}, encryption key (not read or changed), schedule {cfg.get('schedule', {}).get('recurrence')}, "
          f"retention {cfg.get('retention')}")
    if sorted(ALL_FOLDERS) == current:
        ws.close()
        sys.exit("All folders are already included; nothing to do.")
    if apply:
        out = BACKUPS / datetime.now().strftime("%Y%m%d-%H%M%S")
        out.mkdir(parents=True, exist_ok=True)
        (out / "backup_config_before.json").write_text(json.dumps({"include_folders": current}, indent=2), encoding="utf-8")
        ws_raw(ws, "backup/config/update", create_backup={"include_folders": ALL_FOLDERS})
        after = ws_raw(ws, "backup/config/info")["config"]["create_backup"]
        print(f"previous folder setting saved to {out.relative_to(ROOT)}")
        print(f"now: folders {sorted(after.get('include_folders') or [])}, locations {after.get('agent_ids')}, "
              f"database {after.get('include_database')}, all add-ons {after.get('include_all_addons')}, "
              f"encryption key still set: {bool(after.get('password'))}")
    ws.close()


FIRMWARE = {  # update entity -> (physical camera, its camera entity, its motion sensor); names per device, not entity ID
    "update.front_gate_firmware": ("Back Deck", "camera.front_gate_fluent", "binary_sensor.front_gate_motion"),
    "update.homesite_back_deck_firmware": ("Driveway", "camera.homesite_back_deck_fluent", "binary_sensor.homesite_back_deck_motion"),
    "update.homesite_driveway_firmware": ("Front Gate", "camera.homesite_driveway_fluent", "binary_sensor.homesite_driveway_motion"),
}


def _fw_status(ws, eid):
    st = {s["entity_id"]: s for s in ws.call("get_states")}
    name, cam, motion = FIRMWARE[eid]
    u = st.get(eid, {})
    a = u.get("attributes", {})
    return {"camera": name, "update": u.get("state"), "installed": a.get("installed_version"), "latest": a.get("latest_version"),
            "in_progress": a.get("in_progress"), "video": st.get(cam, {}).get("state"), "motion": st.get(motion, {}).get("state"),
            "nvr": st.get("update.nvr_homesite_firmware", {}).get("state")}


def step_firmware(apply):
    """Install one camera's firmware update (update.install), then watch it come back. Usage: firmware <update entity>."""
    import time
    eid = next((a for a in sys.argv[2:] if a.startswith("update.")), None)
    if eid not in FIRMWARE:
        sys.exit(f"Name one update entity: {', '.join(f'{k} ({v[0]})' for k, v in FIRMWARE.items())}")
    ws = h.HAWebSocket()
    require_admin(ws)
    before = _fw_status(ws, eid)
    print(f"{before['camera']} camera ({eid}): {before['installed']} -> {before['latest']}; now: video {before['video']}, "
          f"motion sensor {before['motion']}")
    if before["update"] != "on" or before["in_progress"]:
        ws.close()
        sys.exit("No update pending (or one already in progress); nothing to do.")
    print("will send: update.install (no version pinned, no Home Assistant backup; the camera reboots, ~5-10 min offline)")
    if not apply:
        ws.close()
        return
    ws.ws.settimeout(900)  # Home Assistant holds the call open until the camera has taken the firmware
    ws_raw(ws, "call_service", domain="update", service="install", service_data={}, target={"entity_id": eid})
    ws.ws.settimeout(30)
    print(f"{datetime.now():%H:%M:%S} install finished on the Home Assistant side")
    for _ in range(18):  # watch up to ~9 minutes; re-run 'firmware-status' afterwards if needed
        time.sleep(30)
        s = _fw_status(ws, eid)
        print(f"{datetime.now():%H:%M:%S} update {s['update']}, in progress {s['in_progress']}, installed {s['installed']}, "
              f"video {s['video']}, motion {s['motion']}")
        if s["installed"] == before["latest"] and s["video"] not in ("unavailable", None) and s["motion"] not in ("unavailable", None):
            print("DONE: new firmware reported, video and motion sensor back")
            break
    ws.close()


def step_firmware_status(apply):
    """Read-only: firmware and online state of the three cameras."""
    ws = h.HAWebSocket()
    for eid in FIRMWARE:
        print(_fw_status(ws, eid))
    ws.close()


RELOAD_DEVICES = {"update.nvr_homesite_firmware": "NVR Homesite (and its cameras)",
                  "update.basement_basement_cam_firmware": "Basement Cam"}


def step_reolink_reload(apply):
    """Reload the Reolink entries behind the NVR and the Basement Cam so they re-check firmware. No other change."""
    ws = h.HAWebSocket()
    require_admin(ws)
    reg = {e["entity_id"]: e for e in ws.call("config/entity_registry/list")}
    entries = {e["entry_id"]: e for e in ws_raw(ws, "config_entries/get", domain="reolink")}
    targets = []
    for eid, label in RELOAD_DEVICES.items():
        entry = entries.get(reg.get(eid, {}).get("config_entry_id"))
        if not entry:
            ws.close()
            sys.exit(f"No Reolink entry found for {label}; stopping.")
        targets.append((entry["entry_id"], label, entry["title"], entry["state"]))
        print(f"reload Reolink entry '{entry['title']}' ({label}), currently {entry['state']}")
    print("Home Assistant reconnects to these devices (~1 min, their entities briefly unavailable in HA). "
          "Recording on the NVR and cameras is not affected.")
    before = {e: s["state"] for s in ws.call("get_states") for e in (s["entity_id"],) if e.startswith("update.")}
    if apply:
        import time
        for entry_id, label, title, _ in targets:
            res = rest("POST", f"/api/config/config_entries/entry/{entry_id}/reload")
            print(f"reloaded '{title}': {res}")
        time.sleep(60)
        after = {s["entity_id"]: s for s in ws.call("get_states") if s["entity_id"].startswith("update.")}
        for e in [*FIRMWARE, *RELOAD_DEVICES]:
            a = after.get(e, {}).get("attributes", {})
            print(f"  {e}: {before.get(e)} -> {after.get(e, {}).get('state')} (installed {a.get('installed_version')}, "
                  f"latest {a.get('latest_version')})")
    ws.close()


DECK_CAM_LIGHT = "light.backyard_cam_floodlight"  # Tuya Local duplicate of light.backyard_floodlight (cloud Tuya)


def _strip_entity(o, e, counts):
    """Remove every use of entity e: cards/chips that target it, list items, and its spots in template strings."""
    if isinstance(o, dict):
        for k, v in list(o.items()):
            o[k] = _strip_entity(v, e, counts)
        return o
    if isinstance(o, list):
        keep = []
        for v in o:
            if v == e or (isinstance(v, dict) and (v.get("entity") == e or (isinstance(v.get("chip"), dict) and v["chip"].get("entity") == e))):
                counts["items"] += 1
                continue
            keep.append(_strip_entity(v, e, counts))
        return keep
    if isinstance(o, str) and e in o:
        new = o
        for pat in (f"'{e}': 'Deck Cam Light', ", f"'{e}', ", f", '{e}'"):
            new = new.replace(pat, "")
        counts["templates"] += 1
        return new
    return o


def step_remove_deck_cam_light(apply):
    """Dashboard: remove the Tuya Local 'Deck Cam Light' duplicate everywhere (before disabling Tuya Local)."""
    ws = h.HAWebSocket()
    require_admin(ws)
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    per_view = {}
    for v in dash["views"]:
        if DECK_CAM_LIGHT in json.dumps(v):
            c = {"items": 0, "templates": 0}
            _strip_entity(v, DECK_CAM_LIGHT, c)
            per_view[v.get("path")] = c
    left = json.dumps(dash).count(DECK_CAM_LIGHT)
    for p, c in per_view.items():
        print(f"view '{p}': {c['items']} card/chip/list item(s) removed, {c['templates']} template(s) edited")
    if left:
        ws.close()
        sys.exit(f"{left} reference(s) would remain; stopping without saving.")
    (PAYLOADS / "dashboard_without_deck_cam_light.json").write_text(json.dumps(dash, indent=2, ensure_ascii=False), encoding="utf-8")
    print("0 references left; preview saved to ha-dashboard/outfit/payloads/dashboard_without_deck_cam_light.json")
    if apply:
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    ws.close()


def step_disable_tuya_local(apply):
    """Disable (not delete) the Tuya Local 'Backyard' entry. Reversible: Enable in the same menu."""
    ws = h.HAWebSocket()
    require_admin(ws)
    entries = [e for e in ws_raw(ws, "config_entries/get", domain="tuya_local") if e["title"] == "Backyard"]
    if len(entries) != 1:
        ws.close()
        sys.exit(f"Expected one Tuya Local entry 'Backyard', found {len(entries)}; stopping.")
    e = entries[0]
    live = json.dumps(ws.call("lovelace/config", url_path=DASHBOARD, force=False))
    if DECK_CAM_LIGHT in live:
        ws.close()
        sys.exit("The dashboard still uses the Deck Cam Light; run remove-deck-cam-light first.")
    print(f"disable Tuya Local entry '{e['title']}' (now {e['state']}, disabled_by {e.get('disabled_by')}) -> disabled by user")
    print("its 24 entities become disabled (kept, not deleted); the cloud Tuya 'Backyard' camera is untouched")
    if apply:
        res = ws_raw(ws, "config_entries/disable", entry_id=e["entry_id"], disabled_by="user")
        after = next(x for x in ws_raw(ws, "config_entries/get", domain="tuya_local") if x["entry_id"] == e["entry_id"])
        st = {s["entity_id"]: s["state"] for s in ws.call("get_states")}
        print(f"done: state {after['state']}, disabled_by {after.get('disabled_by')}, require_restart {res.get('require_restart')}; "
              f"cloud Backyard camera: {st.get('camera.backyard')}, floodlight: {st.get('light.backyard_floodlight')}")
    ws.close()


ABODE = "alarm_control_panel.abode_alarm"
IOTA_CAM = "camera.great_room_great_room_2_cam"


def _alarm_card():
    s = f"states('{ABODE}')"
    dw, mo = "binary_sensor.door_window_sensor", "binary_sensor.iota_motion"
    return {
        "type": "custom:mushroom-template-card", "entity": ABODE,
        "icon": (f"{{% set s = {s} %}}{{{{ 'mdi:shield-off-outline' if s == 'disarmed' else ('mdi:shield-alert' if s == 'triggered' "
                 "else ('mdi:shield-home' if s == 'armed_home' else 'mdi:shield-lock')) }}"),
        # sage = armed and safe, amber = arming/pending, rose = triggered, muted = disarmed
        "color": (f"{{% set s = {s} %}}{{% if s == 'triggered' %}}var(--hp-bad){{% elif s in ['arming', 'pending'] %}}var(--hp-warn)"
                  "{% elif s.startswith('armed') %}var(--hp-ok){% else %}var(--hp-muted){% endif %}"),
        "primary": f"Alarm · {{{{ state_translated('{ABODE}') }}}}",
        "secondary": (f"Door/window {{{{ 'open' if is_state('{dw}', 'on') else 'closed' }}}} · "
                      f"motion {{{{ 'detected' if is_state('{mo}', 'on') else 'clear' }}}}"),
        "tap_action": {"action": "more-info"}, "hold_action": {"action": "none"}}


def _alarm_modes():
    def mode(label, icon, state, service, confirm=None):
        tap = {"action": "perform-action", "perform_action": f"alarm_control_panel.{service}", "target": {"entity_id": ABODE}}
        if confirm:
            tap["confirmation"] = {"text": confirm}
        return _chip(label, icon, tap, {"action": "more-info"},
                     icon_color=f"{{{{ 'yellow' if is_state('{ABODE}', '{state}') else 'grey' }}}}", entity=ABODE)
    return {"type": "custom:mushroom-chips-card", "alignment": "center", "chips": [
        mode("Home", "mdi:shield-home", "armed_home", "alarm_arm_home"),
        mode("Away", "mdi:shield-lock", "armed_away", "alarm_arm_away"),
        mode("Off", "mdi:shield-off-outline", "disarmed", "alarm_disarm", "Disarm the Abode alarm?")]}


def step_abode_dashboard(apply):
    """Security page: Abode alarm card + Home/Away/Off (disarm asks first). Cameras: Iota snapshot card."""
    ws = h.HAWebSocket()
    require_admin(ws)
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    secs = dash["views"][0]["sections"]
    page = next(c["cards"][0] for s in secs for c in s.get("cards", [])
                if c.get("type") == "horizontal-stack" and c["cards"][0]["cards"][0].get("heading") == "Security")
    cams = next(s for s in secs if s["cards"] and s["cards"][0].get("heading") == "Cameras")
    if any(ABODE in json.dumps(c) for c in page["cards"]) or any(c.get("entity") == IOTA_CAM for c in cams["cards"]):
        ws.close()
        sys.exit("Abode cards are already on the dashboard; nothing to do.")
    ref = next(c for c in cams["cards"] if c.get("type") == "picture-entity")
    iota = {**ref, "entity": IOTA_CAM, "name": "Great Room · Iota",
            "visibility": [{"condition": "state", "entity": IOTA_CAM, "state_not": "unavailable"}]}
    print("Security page: insert under the heading -> Alarm card (state, door/window, motion; tap = full alarm dialog)")
    print("               + Home / Away / Off pills (active one gold; Off asks 'Disarm the Abode alarm?')")
    print(f"Cameras: add '{iota['name']}' snapshot card (same style: online dot, hidden if unavailable); "
          f"{sum(1 for c in cams['cards'] if c.get('type') == 'picture-entity')} -> "
          f"{sum(1 for c in cams['cards'] if c.get('type') == 'picture-entity') + 1} cameras")
    if apply:
        page["cards"][1:1] = [_alarm_card(), _alarm_modes()]
        cams["cards"].append(iota)
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    else:
        (PAYLOADS / "abode_cards.json").write_text(json.dumps({"alarm": _alarm_card(), "modes": _alarm_modes(), "iota": iota},
                                                              indent=2, ensure_ascii=False), encoding="utf-8")
    ws.close()


def step_iota_snapshot(apply):
    """Ask the Iota to capture one still image (abode.capture_image). Nothing is downloaded or viewed."""
    print(f"will send: abode.capture_image on {IOTA_CAM}")
    if apply:
        ws = h.HAWebSocket()
        require_admin(ws)
        ws_raw(ws, "call_service", domain="abode", service="capture_image", service_data={}, target={"entity_id": IOTA_CAM})
        ws.close()
        print("capture requested; the card updates when Abode delivers the image")


MOWER = "lawn_mower.mower"
MS = "sensor.mower_"
MOWER_CARE = [("blade", "Blades", "mdi:saw-blade"), ("edge_trimmer_brush", "Edge brush", "mdi:brush"),
              ("edge_trimmer_line", "Trim line", "mdi:vector-curve"), ("lens_brush", "Lens", "mdi:eye-outline")]


def _mower_page(heading_mod, panel_mod, deck_mod):
    err = f"{MS}error"
    no_err = f"states('{err}') in ['0', 'none', 'unknown', 'unavailable', '']"
    s = f"states('{MOWER}')"
    ft2 = f"((states('{MS}area_mowed') | float(0)) * 10.7639) | round(0) | int"
    mins = f"states('{MS}mowing_duration') | float(0) | round(0) | int"
    ev = "event.mower_last_job"
    heading = {"type": "heading", "heading": "Mower", "heading_style": "title", "icon": "mdi:robot-mower", "badges": [
        {"type": "entity", "entity": f"{MS}blade_lifespan", "icon": "mdi:saw-blade", "name": "Blades", "show_state": True,
         "show_icon": True, "color": "var(--hp-bad)", "tap_action": {"action": "more-info"},
         "visibility": [{"condition": "numeric_state", "entity": f"{MS}blade_lifespan", "below": 15}]},
        {"type": "entity", "entity": f"{MS}battery", "show_state": True, "show_icon": True, "tap_action": {"action": "more-info"}}]}
    status = {
        "type": "custom:mushroom-template-card", "entity": MOWER, "icon": "mdi:robot-mower", "multiline_secondary": True,
        "color": (f"{{% set s = {s} %}}{{% if not ({no_err}) or s == 'error' %}}var(--hp-bad){{% elif s in ['mowing', 'returning'] %}}"
                  "var(--hp-gold){% elif s == 'paused' %}var(--hp-warn){% elif s == 'docked' %}var(--hp-ok){% else %}var(--hp-muted){% endif %}"),
        "badge_icon": (f"{{% if not ({no_err}) %}}mdi:alert{{% elif (states('{MS}blade_lifespan') | float(100)) < 15 %}}mdi:saw-blade{{% endif %}}"),
        "badge_color": "var(--hp-bad)",
        "primary": (f"{{% set s = {s} %}}{{% if not ({no_err}) %}}Needs help · {{{{ state_attr('{err}', 'description') or state_translated('{err}') }}}}"
                    f"{{% elif s == 'docked' %}}Docked · {{{{ 'Charged' if (states('{MS}battery') | float(0)) >= 100 else 'Charging' }}}}"
                    f"{{% else %}}{{{{ state_translated('{MOWER}') }}}}{{% endif %}}"),
        "secondary": (f"{{% if {s} == 'mowing' %}}{{{{ {mins} }}}} min · {{{{ {ft2} }}}} ft² so far"
                      f"{{% elif has_value('{ev}') %}}{{% set t = state_attr('{ev}', 'event_type') %}}"
                      "Last mow {{ {'finished': 'finished', 'finished_with_warnings': 'finished with warnings', 'manually_stopped': 'stopped'}.get(t, 'ended') }} "
                      f"{{{{ relative_time(as_datetime(states('{ev}'))) }}}} ago{{% if ({mins}) > 0 %}} · {{{{ {mins} }}}} min · {{{{ {ft2} }}}} ft²{{% endif %}}"
                      f"{{% else %}}{{{{ states('{MS}total_mowings') }}}} mows · {{{{ states('{MS}total_mowing_duration') | float(0) | round(0) | int }}}} h lifetime"
                      "{% endif %}"),
        "tap_action": {"action": "more-info"}, "hold_action": {"action": "none"}}
    btn = lambda label, icon, color, tap: {**_action_button(label, icon, color, tap), "card_mod": {"style": DECK_BUTTON}}  # noqa: E731
    lm = lambda svc, confirm=None: {**_run(f"lawn_mower.{svc}", target=MOWER), **({"confirmation": {"text": confirm}} if confirm else {})}  # noqa: E731
    deck = {"type": "vertical-stack", "card_mod": deck_mod, "cards": [{"type": "grid", "columns": 3, "square": False, "cards": [
        btn("Mow", "mdi:play", "var(--hp-gold)", lm("start_mowing", "Start mowing now? Make sure the yard is clear of pets and people.")),
        btn("Pause", "mdi:pause", f"{{{{ 'var(--hp-gold)' if {s} == 'mowing' else 'var(--hp-muted)' }}}}", lm("pause")),
        btn("Dock", "mdi:home-import-outline", f"{{{{ 'var(--hp-muted)' if {s} == 'docked' else 'var(--hp-ok)' }}}}", lm("dock")),
    ]}]}
    care = [_chip(f"{label} {{{{ states('{MS}{k}_lifespan') | float(0) | round(0) | int }}}}%", icon, {"action": "more-info"},
                  icon_color=f"{{% set p = states('{MS}{k}_lifespan') | float(100) %}}{{{{ 'red' if p < 15 else ('amber' if p < 30 else '') }}}}",
                  entity=f"{MS}{k}_lifespan") for k, label, icon in MOWER_CARE]
    care.append(_chip(f"{{{{ states('{MS}total_mowings') }}}} mows · {{{{ states('{MS}total_mowing_duration') | float(0) | round(0) | int }}}} h · "
                      f"{{{{ ((states('{MS}total_area_mowed') | float(0)) / 1000) | round(0) | int }}}}k ft²",
                      "mdi:chart-timeline-variant", {"action": "none"}, icon_color="grey"))
    return {"type": "vertical-stack", "cards": [heading, status, deck, _panel("Care", care, heading_mod, panel_mod), _dots("○  ●  ○")]}


def step_mower_page(apply):
    """Enable the mower's Error sensor; add a Mower page between Cleaning and Media (dots updated to three pages)."""
    ws = h.HAWebSocket()
    require_admin(ws)
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    swipe = next(c for s in dash["views"][0]["sections"] for c in s.get("cards", [])
                 if c.get("type") == "horizontal-stack" and c["cards"][0]["cards"][0].get("heading") == "Cleaning")
    if any(p["cards"][0].get("heading") == "Mower" for p in swipe["cards"]):
        ws.close()
        sys.exit("Mower page already exists; nothing to do.")
    clean, media = swipe["cards"][0], swipe["cards"][-1]
    rooms = next(p for p in clean["cards"] if p.get("type") == "vertical-stack" and p["cards"][0].get("heading") == "Upstairs")
    deck = next(p for p in clean["cards"] if p.get("type") == "vertical-stack" and p["cards"][0].get("type") == "grid")
    page = _mower_page(rooms["cards"][0]["card_mod"], rooms["card_mod"], deck["card_mod"])
    reg = ws_raw(ws, "config/entity_registry/get", entity_id=f"{MS}error")
    print(f"sensor.mower_error: disabled_by {reg.get('disabled_by')} -> enabled (Home Assistant reloads the Ecovacs entry ~30 s later)")
    print("Cleaning swipe: Cleaning | Mower (new) | Media; dots ●○○ / ○●○ / ○○●")
    print("Mower page: heading (blade warning under 15%, battery), status card, deck Mow (asks first) · Pause · Dock, Care pills + lifetime")
    (PAYLOADS / "mower_page.json").write_text(json.dumps(page, indent=2, ensure_ascii=False), encoding="utf-8")
    if apply:
        if reg.get("disabled_by"):
            res = ws_raw(ws, "config/entity_registry/update", entity_id=f"{MS}error", disabled_by=None)
            print(f"error sensor enabled (reload in {res.get('reload_delay', '?')} s)")
        for p, dots in ((clean, "●  ○  ○"), (media, "○  ○  ●")):
            for c in p["cards"]:
                if c.get("type") == "markdown" and "●" in str(c.get("content")):
                    c["content"] = dots
        swipe["cards"].insert(1, page)
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    ws.close()


PRESENCE = {  # old entity prefix -> (new prefix, display name, area name, far detection cm or None to leave)
    "presence_sensor": ("kitchen_presence", "Kitchen Presence", "Kitchen", 300),
    "presence_sensor_2": ("basement_presence", "Basement Presence", "Basement", None),
}
PRESENCE_SUFFIX = {"binary_sensor": {"occupancy": ""}, "number": {"sensitivity": "_sensitivity", "far_detection": "_far_detection",
                                                                  "closest_target_distance": "_closest_target_distance"}}


def step_presence_setup(apply):
    """Name, room and entity IDs for the two Tuya presence sensors; Kitchen far detection 300 cm (sensitivity unchanged)."""
    ws = h.HAWebSocket()
    require_admin(ws)
    areas = {a["name"]: a["area_id"] for a in ws.call("config/area_registry/list")}
    reg = {e["entity_id"]: e for e in ws.call("config/entity_registry/list")}
    st = {s["entity_id"]: s["state"] for s in ws.call("get_states")}
    plan, devices = [], {}
    for old, (new, name, area, far) in PRESENCE.items():
        for dom, sufs in PRESENCE_SUFFIX.items():
            for suf, new_suf in sufs.items():
                eid = f"{dom}.{old}_{suf}"
                if eid not in reg:
                    ws.close()
                    sys.exit(f"{eid} not found; stopping.")
                label = name if not new_suf else f"{name} {new_suf.strip('_').replace('_', ' ')}"
                plan.append((eid, f"{dom}.{new}{new_suf}", label))
                devices[reg[eid]["device_id"]] = (name, areas[area])
    for eid, new_id, label in plan:
        print(f"{eid:48} -> {new_id:48} name '{label}'")
    for did, (name, aid) in devices.items():
        print(f"device -> '{name}', area {aid}")
    far_eid = "number.presence_sensor_far_detection"
    print(f"Kitchen far detection: {st.get(far_eid)} cm -> 300 cm (~9.8 ft); sensitivity stays {st.get('number.presence_sensor_sensitivity')}")
    if apply:
        ws_raw(ws, "call_service", domain="number", service="set_value", service_data={"value": 300}, target={"entity_id": far_eid})
        for did, (name, aid) in devices.items():
            ws_raw(ws, "config/device_registry/update", device_id=did, name_by_user=name, area_id=aid)
        for eid, new_id, label in plan:
            ws_raw(ws, "config/entity_registry/update", entity_id=eid, new_entity_id=new_id, name=label)
        st = {s["entity_id"]: s["state"] for s in ws.call("get_states")}
        print("done:", {k: st.get(k) for k in ("binary_sensor.kitchen_presence", "binary_sensor.basement_presence",
                                               "number.kitchen_presence_far_detection", "number.kitchen_presence_sensitivity")})
    ws.close()


def _presence_chip(eid):
    return _chip(f"{{{{ 'Occupied' if is_state('{eid}', 'on') else 'Clear' }}}}",
                 f"{{{{ 'mdi:account' if is_state('{eid}', 'on') else 'mdi:account-outline' }}}}", {"action": "more-info"},
                 {"action": "more-info"}, icon_color=f"{{{{ 'yellow' if is_state('{eid}', 'on') else 'grey' }}}}", entity=eid)


def _add_presence_to_panels(grid):
    """Kitchen panel: presence pill first. Hallways panel -> 'Basement & halls' with the basement pill; shown if the sensor is up."""
    done = []
    for p in grid["cards"]:
        head, chips = p["cards"][0], p["cards"][1]["chips"]
        if any("presence" in str(c.get("entity") or c.get("chip", {}).get("entity")) for c in chips):
            continue
        if head.get("heading") == "Kitchen":
            chips.insert(0, _presence_chip("binary_sensor.kitchen_presence"))
            done.append("Kitchen")
        elif head.get("heading") == "Hallways":
            head["heading"] = "Basement & halls"
            chips.insert(0, _presence_chip("binary_sensor.basement_presence"))
            for vis in (p.get("visibility") or [], p["cards"][1].get("visibility") or []):
                for cond in vis:
                    if cond.get("condition") == "or":
                        cond["conditions"].append({"condition": "state", "entity": "binary_sensor.basement_presence",
                                                   "state_not": "unavailable"})
            done.append("Hallways -> Basement & halls")
    return done


def step_presence_dashboard(apply):
    """Presence pills on the Lights swipe page and the Lights shortcut page (kept identical)."""
    ws = h.HAWebSocket()
    require_admin(ws)
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    swipe = next(c for s in dash["views"][0]["sections"] for c in s.get("cards", [])
                 if c.get("type") == "horizontal-stack" and c["cards"][1]["cards"][0].get("heading") == "Lights & plugs")
    sub = next(v for v in dash["views"] if v.get("path") == "lights")
    grids = [next(c for c in swipe["cards"][1]["cards"] if c.get("type") == "grid"),
             next(c for c in sub["sections"][0]["cards"] if c.get("type") == "grid")]
    for where, g in zip(("Home > Lights page", "Lights shortcut page"), grids):
        print(f"{where}: {_add_presence_to_panels(g) or 'already done'}")
    print("pill: 'Occupied' (gold person) / 'Clear' (grey outline); tap or hold = history")
    if apply:
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    ws.close()


KITCHEN_LIGHTS = ["light.kitchen_kitchen_main_lights_2", "light.kitchen_kitchen_main_lights_zone1"]
PRESENCE_AUTOS = {
    "kitchen_presence_lights": {
        "sensor": "binary_sensor.kitchen_presence", "lights": KITCHEN_LIGHTS, "flag": "input_boolean.kitchen_auto_lights",
        "flag_name": "Kitchen auto lights", "off_after": "00:10:00", "dark_only": True,
        "brightness": "{{ 10 if now().hour >= 23 or now().hour < 6 else 70 }}", "alias": "Kitchen: lights follow presence"},
    "basement_presence_lights": {  # the six laundry bulbs, individually (the group command was unreliable)
        "sensor": "binary_sensor.basement_presence",
        "lights": [f"light.laundry_light_{i}" for i in (1, 2, 4, 5, 6)] + ["light.basement_final"],  # light 3 removed 2026-09-27
        "flag": "input_boolean.basement_auto_lights", "flag_name": "Basement auto lights", "off_after": "00:02:00",
        "dark_only": False, "brightness": 100, "alias": "Basement: lights follow presence", "robust": True},
}


def _none_on(lights):
    # offline bulbs count as off, so one dead bulb can't block the rest
    return "{{ " + str(lights) + " | select('is_state', 'on') | list | count == 0 }}"


def _robust_automation(a):
    """One bulb at a time, failures skipped (Tuya cloud errors), one retry; flag set first so switching off still works."""
    lights = a["lights"]
    each = lambda action, items, data=None: {"repeat": {"for_each": items, "sequence": [  # noqa: E731
        {"action": action, "target": {"entity_id": "{{ repeat.item }}"}, **({"data": data} if data else {}), "continue_on_error": True},
        {"delay": {"milliseconds": 300}}]}}
    retry_list = "{{ " + str(lights) + " | reject('is_state', 'on') | reject('is_state', 'unavailable') | list }}"
    auto_or_none_on = ("{{ is_state('" + a["flag"] + "', 'on') or " + _none_on(lights)[3:-3] + " }}")
    return {
        "alias": a["alias"],
        "description": (f"When {a['sensor']} detects someone: turns on each bulb that is off, one at a time, skipping any that "
                        "fail, and retries once after 4 s (only if none were on, or the automation already turned them on). "
                        f"Turns them off {a['off_after'][3:5]} min after the room is clear, but only if this automation turned them on."),
        "mode": "queued",
        "triggers": [
            {"trigger": "state", "entity_id": a["sensor"], "to": "on", "id": "occupied"},
            {"trigger": "state", "entity_id": a["sensor"], "to": "off", "for": a["off_after"], "id": "clear"},
            {"trigger": "state", "entity_id": lights, "to": "off", "id": "lights_off"},
        ],
        "conditions": [],
        "actions": [{"choose": [
            {"conditions": [{"condition": "trigger", "id": "occupied"}, {"condition": "template", "value_template": auto_or_none_on}],
             "sequence": [
                 {"action": "input_boolean.turn_on", "target": {"entity_id": a["flag"]}},
                 each("light.turn_on", retry_list, {"brightness_pct": a["brightness"]}),
                 {"delay": {"seconds": 4}},
                 each("light.turn_on", retry_list, {"brightness_pct": a["brightness"]})]},
            {"conditions": [{"condition": "trigger", "id": "clear"}, {"condition": "state", "entity_id": a["flag"], "state": "on"}],
             "sequence": [each("light.turn_off", "{{ " + str(lights) + " | select('is_state', 'on') | list }}"),
                          {"action": "input_boolean.turn_off", "target": {"entity_id": a["flag"]}}]},
            {"conditions": [{"condition": "trigger", "id": "lights_off"}, {"condition": "template", "value_template": _none_on(lights)}],
             "sequence": [{"action": "input_boolean.turn_off", "target": {"entity_id": a["flag"]}}]},
        ]}],
    }


def _presence_automation(a):
    if a.get("robust"):
        return _robust_automation(a)
    on_conds = [{"condition": "trigger", "id": "occupied"}, {"condition": "template", "value_template": _none_on(a["lights"])}]
    if a["dark_only"]:
        on_conds.append({"condition": "state", "entity_id": "sun.sun", "state": "below_horizon"})
    return {
        "alias": a["alias"],
        "description": (f"Turns the lights on when {a['sensor']} detects someone (only if they are off"
                        + (" and the sun is down" if a["dark_only"] else "") + f"), and off {a['off_after'][3:5]} min after the room is "
                        "clear, but only if this automation turned them on. Lights you switch on yourself are left alone."),
        "mode": "queued",
        "triggers": [
            {"trigger": "state", "entity_id": a["sensor"], "to": "on", "id": "occupied"},
            {"trigger": "state", "entity_id": a["sensor"], "to": "off", "for": a["off_after"], "id": "clear"},
            {"trigger": "state", "entity_id": a["lights"], "to": "off", "id": "lights_off"},
        ],
        "conditions": [],
        "actions": [{"choose": [
            {"conditions": on_conds, "sequence": [
                {"action": "light.turn_on", "target": {"entity_id": a["lights"]}, "data": {"brightness_pct": a["brightness"]}},
                {"action": "input_boolean.turn_on", "target": {"entity_id": a["flag"]}}]},
            {"conditions": [{"condition": "trigger", "id": "clear"}, {"condition": "state", "entity_id": a["flag"], "state": "on"}],
             "sequence": [{"action": "light.turn_off", "target": {"entity_id": a["lights"]}},
                          {"action": "input_boolean.turn_off", "target": {"entity_id": a["flag"]}}]},
            {"conditions": [{"condition": "trigger", "id": "lights_off"},
                            {"condition": "template", "value_template": _none_on(a["lights"])}],
             "sequence": [{"action": "input_boolean.turn_off", "target": {"entity_id": a["flag"]}}]},
        ]}],
    }


def step_presence_automations(apply):
    """Two helpers (remember who turned the lights on) + two automations. Created enabled.
    Optional: 'presence-automations basement' (or kitchen) updates just that one."""
    ws = h.HAWebSocket()
    require_admin(ws)
    existing = {e["entity_id"] for e in ws.call("config/entity_registry/list")}
    only = next((f"{a}_presence_lights" for a in sys.argv[2:] if a in ("basement", "kitchen")), None)
    for key, a in PRESENCE_AUTOS.items():
        if only and key != only:
            continue
        print(f"helper {a['flag']}" + (" (exists)" if a["flag"] in existing else " -> create"))
        show(f"automation '{a['alias']}' (id {key})", _presence_automation(a))
    if apply:
        for key, a in PRESENCE_AUTOS.items():
            if only and key != only:
                continue
            if a["flag"] not in existing:
                ws_raw(ws, "input_boolean/create", name=a["flag_name"], icon="mdi:motion-sensor")
            print(key, rest("POST", f"/api/config/automation/config/{key}", _presence_automation(a)))
        st = {s["entity_id"]: s["state"] for s in ws.call("get_states")}
        print({k: v for k, v in st.items() if "presence_lights" in k or "auto_lights" in k})
    ws.close()


def _merge_basement_laundry(grid):
    """'Basement & halls' + 'Laundry' -> 'Basement & laundry' (presence, laundry group, Basement Entry) and a 'Hallways'
    panel for the two hallway lights (hidden while both are offline, as before)."""
    panels = {p["cards"][0].get("heading"): p for p in grid["cards"]}
    if "Basement & laundry" in panels:
        return "already done"
    bh, laundry = panels.get("Basement & halls"), panels.get("Laundry")
    if not bh or not laundry:
        raise RuntimeError(f"panels not found: {list(panels)}")
    chips = bh["cards"][1]["chips"]
    ent = lambda c: c.get("entity") or c.get("chip", {}).get("entity")  # noqa: E731
    presence = [c for c in chips if ent(c) == "binary_sensor.basement_presence"]
    entry = [c for c in chips if ent(c) == "light.basement_entry"]
    halls = [c for c in chips if ent(c) in ("light.bedroom_hallway_light", "light.guest_room_hallway_light")]
    import copy
    hallways = copy.deepcopy(bh)
    hallways["cards"][0]["heading"] = "Hallways"
    hallways["cards"][1]["chips"] = halls
    vis = [{"condition": "or", "conditions": [{"condition": "state", "entity": ent(c), "state_not": "unavailable"} for c in halls]}]
    hallways["visibility"] = vis
    hallways["cards"][1]["visibility"] = vis
    laundry["cards"][0]["heading"] = "Basement & laundry"
    laundry["cards"][1]["chips"] = presence + laundry["cards"][1]["chips"] + entry
    laundry.pop("visibility", None)
    i = grid["cards"].index(bh)
    grid["cards"][i] = hallways
    return "Laundry -> 'Basement & laundry' (presence, Laundry, Basement Entry when online); 'Basement & halls' -> 'Hallways' (hidden while offline)"


def step_basement_laundry(apply):
    ws = h.HAWebSocket()
    require_admin(ws)
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    swipe = next(c for s in dash["views"][0]["sections"] for c in s.get("cards", [])
                 if c.get("type") == "horizontal-stack" and c["cards"][1]["cards"][0].get("heading") == "Lights & plugs")
    sub = next(v for v in dash["views"] if v.get("path") == "lights")
    for where, g in (("Home > Lights page", next(c for c in swipe["cards"][1]["cards"] if c.get("type") == "grid")),
                     ("Lights shortcut page", next(c for c in sub["sections"][0]["cards"] if c.get("type") == "grid"))):
        print(f"{where}: {_merge_basement_laundry(g)}")
        print("   panels now:", [p["cards"][0].get("heading") for p in g["cards"]])
    if apply:
        ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=dash)
        print("dashboard saved")
    ws.close()


BASEMENT_GROUP = [f"light.laundry_light_{i}" for i in (1, 2, 4, 5, 6)] + ["light.basement_final"]


def step_basement_final(apply):
    """Replace the removed Laundry light 3 with the new 'Basement Final' bulb everywhere; try to drop the stale device."""
    ws = h.HAWebSocket()
    require_admin(ws)
    reg = {e["entity_id"]: e for e in ws.call("config/entity_registry/list")}
    new_exists = "light.basement_final" in reg
    if not new_exists and "light.bulb_rgbw_a_10" not in reg:
        ws.close()
        sys.exit("Basement Final bulb not found; stopping.")
    areas = {a["name"]: a["area_id"] for a in ws.call("config/area_registry/list")}
    grp = next(e for e in ws_raw(ws, "config_entries/get", domain="group") if e["title"].lower().startswith("laundry"))
    dash = ws.call("lovelace/config", url_path=DASHBOARD, force=False)
    text = json.dumps(dash, ensure_ascii=False)
    refs = text.count("light.laundry_light_3")
    print(("1. light.bulb_rgbw_a_10 -> light.basement_final, name 'Basement Final', area Basement" if not new_exists
           else "1. light.basement_final already renamed"))
    print(f"2. group '{grp['title']}': members -> {', '.join(e.split('.')[1] for e in BASEMENT_GROUP)}")
    print("3. automation 'Basement: lights follow presence': lights -> the same 6")
    print(f"4. dashboard: {refs} reference(s) to laundry_light_3 -> basement_final (lists, templates, room tile, review row)")
    d3 = reg.get("light.laundry_light_3")
    print("5. remove the stale 'laundry light 3' device from Home Assistant (Tuya may refuse if it's still in your account)"
          if d3 else "5. laundry light 3 already gone")
    if not apply:
        ws.close()
        return
    if not new_exists:
        e = reg["light.bulb_rgbw_a_10"]
        ws_raw(ws, "config/entity_registry/update", entity_id="light.bulb_rgbw_a_10", new_entity_id="light.basement_final",
               name="Basement Final")
        ws_raw(ws, "config/device_registry/update", device_id=e["device_id"], name_by_user="Basement Final", area_id=areas["Basement"])
        print("   renamed")
    flow = rest("POST", "/api/config/config_entries/options/flow", {"handler": grp["entry_id"]})
    res = rest("POST", f"/api/config/config_entries/options/flow/{flow['flow_id']}",
               {"entities": BASEMENT_GROUP, "hide_members": False, "all": False})
    print(f"   group options: {res.get('type')}")
    a = PRESENCE_AUTOS["basement_presence_lights"]
    print("   automation:", rest("POST", "/api/config/automation/config/basement_presence_lights", _presence_automation(a)))
    text = (text.replace("light.laundry_light_3", "light.basement_final").replace('"name": "Laundry Light 3"', '"name": "Basement Final"')
            .replace("| laundry light 3 |", "| Basement Final |"))
    ws_raw(ws, "lovelace/config/save", url_path=DASHBOARD, config=json.loads(text))
    print("   dashboard saved")
    if d3:
        try:
            ws_raw(ws, "config/device_registry/remove_config_entry", device_id=d3["device_id"], config_entry_id=d3["config_entry_id"])
            print("   stale device removed")
        except RuntimeError as err:
            print(f"   Tuya kept the device ({h.scrub(err)[:80]}); remove 'laundry light 3' in the Smart Life app, then reload Tuya")
    st = {s["entity_id"]: s for s in ws.call("get_states")}
    print("   group members now:", st.get("light.laundry_lights", {}).get("attributes", {}).get("entity_id"))
    ws.close()


ADDON_UPDATES = {"update.advanced_ssh_web_terminal_update": "Advanced SSH & Web Terminal"}


def step_addon_update(apply):
    """Install one add-on update (update.install, no extra backup), then confirm the add-on is running the new version."""
    import time
    eid = next((a for a in sys.argv[2:] if a.startswith("update.")), "update.advanced_ssh_web_terminal_update")
    if eid not in ADDON_UPDATES:
        sys.exit(f"Unknown add-on update: {eid}")
    name = ADDON_UPDATES[eid]
    ws = h.HAWebSocket()
    require_admin(ws)
    st = {s["entity_id"]: s for s in ws.call("get_states")}
    a = st.get(eid, {}).get("attributes", {})
    if st.get(eid, {}).get("state") != "on":
        ws.close()
        sys.exit(f"No update pending for {name}; nothing to do.")
    info = ws_raw(ws, "backup/info")
    newest = max(info.get("backups", []), key=lambda b: b.get("date") or "")
    addon = next(x for x in ws_raw(ws, "supervisor/api", endpoint="/addons", method="get")["addons"] if x.get("name") == name)
    print(f"{name}: {a.get('installed_version')} -> {a.get('latest_version')}; add-on now {addon.get('state')}")
    print(f"rollback point: backup {newest['date'][:16]} with {len(newest.get('addons') or [])} add-ons (includes this one)")
    print("will send: update.install (no extra backup). The add-on restarts; an open terminal session disconnects.")
    if not apply:
        ws.close()
        return
    ws.ws.settimeout(900)
    ws_raw(ws, "call_service", domain="update", service="install", service_data={}, target={"entity_id": eid})
    ws.ws.settimeout(30)
    for _ in range(20):
        s = {x["entity_id"]: x for x in ws.call("get_states")}[eid]
        ad = next(x for x in ws_raw(ws, "supervisor/api", endpoint="/addons", method="get")["addons"] if x.get("name") == name)
        if s["state"] == "off" and ad.get("state") == "started":
            print(f"DONE: installed {s['attributes'].get('installed_version')}, add-on {ad.get('state')}")
            break
        time.sleep(3)
    else:
        print(f"not confirmed yet: update {s['state']}, add-on {ad.get('state')}; check Settings → Add-ons")
    ws.close()


TV_FIREPLACE = {
    "alias": "Great Room: fireplace follows the Sony TV",
    "description": ("Turns the fireplace on when the Sony TV has been playing for 10 s (TV itself or a cast app), and off when "
                    "the TV has been off for 10 s. Safety: also off when the Abode alarm is armed Away."),
    "mode": "restart",
    "triggers": [
        {"trigger": "state", "entity_id": ["media_player.great_room_sony_tv", "media_player.sony_tv"], "to": "playing",
         "for": "00:00:10", "id": "playing"},
        {"trigger": "state", "entity_id": "media_player.great_room_sony_tv", "to": "off", "for": "00:00:10", "id": "tv_off"},
        {"trigger": "state", "entity_id": "alarm_control_panel.abode_alarm", "to": "armed_away", "id": "away"},
    ],
    "conditions": [],
    "actions": [{"choose": [
        {"conditions": [{"condition": "trigger", "id": "playing"}, {"condition": "state", "entity_id": "switch.wemo_mini", "state": "off"}],
         "sequence": [{"action": "switch.turn_on", "target": {"entity_id": "switch.wemo_mini"}}]},
        {"conditions": [{"condition": "trigger", "id": ["tv_off", "away"]}, {"condition": "state", "entity_id": "switch.wemo_mini", "state": "on"}],
         "sequence": [{"action": "switch.turn_off", "target": {"entity_id": "switch.wemo_mini"}}]},
    ]}],
}


def step_tv_fireplace(apply):
    """Create the 'fireplace follows the Sony TV' automation (enabled)."""
    ws = h.HAWebSocket()
    require_admin(ws)
    st = {s["entity_id"]: s["state"] for s in ws.call("get_states")}
    for e in ("media_player.great_room_sony_tv", "media_player.sony_tv", "switch.wemo_mini", "alarm_control_panel.abode_alarm"):
        print(f"  {e}: {st.get(e, 'MISSING')}")
    if any(e not in st for e in ("media_player.great_room_sony_tv", "media_player.sony_tv", "switch.wemo_mini")):
        ws.close()
        sys.exit("A required entity is missing; stopping.")
    show("automation tv_fireplace", TV_FIREPLACE)
    if apply:
        print(rest("POST", "/api/config/automation/config/tv_fireplace", TV_FIREPLACE))
        st = {s["entity_id"]: s["state"] for s in ws.call("get_states")}
        print("automation:", {k: v for k, v in st.items() if "fireplace_follows" in k})
    ws.close()


def step_ha_backup(apply):
    """Create a Home Assistant backup now with the automatic-backup settings (same as Back up now → Automatic)."""
    import time
    ws = h.HAWebSocket()
    require_admin(ws)
    before = ws_raw(ws, "backup/info")
    n = len(before.get("backups", []))
    retention = (ws_raw(ws, "backup/config/info")["config"].get("retention") or {}).get("copies")
    print(f"backups now: {n}; retention keeps {retention} automatic-settings backups (the oldest is removed by Home Assistant if exceeded)")
    print("will send: backup/generate_with_automatic_settings")
    if apply:
        ws_raw(ws, "backup/generate_with_automatic_settings")
        for _ in range(40):
            time.sleep(3)
            info = ws_raw(ws, "backup/info")
            newest = max(info.get("backups", []), key=lambda b: b.get("date") or "", default={})
            if newest and newest.get("date", "") > max((b.get("date") or "" for b in before.get("backups", [])), default=""):
                loc = newest.get("agents") or {}
                print(f"done: {newest['date'][:16]}, {sum((v or {}).get('size') or 0 for v in loc.values()) / 1e6:.0f} MB, "
                      f"encrypted {all((v or {}).get('protected') for v in loc.values())}, failed {newest.get('failed_agent_ids') or 'none'}; "
                      f"{len(info['backups'])} backups stored")
                break
        else:
            print("not finished after 2 minutes; check Settings → System → Backups")
    ws.close()


def _set_departure(ws, hhmmss):
    ws_raw(ws, "call_service", domain="input_datetime", service="set_datetime", service_data={"time": hhmmss},
           target={"entity_id": "input_datetime.work_departure"})


def step_test_on(apply):
    """Temporary: include commute info on any day, with departure at 23:30 tonight."""
    body = json.loads((PAYLOADS / "script.json").read_text(encoding="utf-8"))
    var = next(s for s in body["sequence"] if "variables" in s)["variables"]
    if "now().weekday() < 5 and " not in var["commute_mode"]:
        sys.exit("Weekday rule not found in script payload; stopping.")
    var["commute_mode"] = var["commute_mode"].replace("now().weekday() < 5 and ", "")
    print("TEST script commute_mode:", var["commute_mode"])
    print("TEST departure: 23:30:00")
    if apply:
        ws = h.HAWebSocket(); require_admin(ws)
        print(rest("POST", f"/api/config/script/config/{SCRIPT_KEY}", body))
        _set_departure(ws, "23:30:00"); ws.close()
        print("test mode ON")


def step_test_off(apply):
    """Restore the normal script (weekdays only) and 7:00 departure."""
    body = json.loads((PAYLOADS / "script.json").read_text(encoding="utf-8"))
    print("RESTORE script from payloads/script.json and departure", DEPARTURE)
    if apply:
        ws = h.HAWebSocket(); require_admin(ws)
        print(rest("POST", f"/api/config/script/config/{SCRIPT_KEY}", body))
        _set_departure(ws, DEPARTURE); ws.close()
        print("test mode OFF")


def step_run(apply):
    """Run the advice script once (one OpenAI request), then show the resulting helper values."""
    print("call_service script.refresh_weather_clothing_advice (blocking)")
    if not apply:
        return
    ws = h.HAWebSocket(); require_admin(ws)
    ws_raw(ws, "call_service", domain="script", service="refresh_weather_clothing_advice", service_data={}, target={})
    for s in ws.call("get_states"):
        if s["entity_id"] in ("input_text.outfit_headline", "input_text.outfit_icon", "input_text.weather_clothing_advice", "input_datetime.outfit_updated"):
            print(f"  {s['entity_id']}: {s['state']}")
    ws.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    step, apply = (args[0] if args else ""), "--apply" in args
    steps = {"helpers": step_helpers, "script": step_script, "automation": step_automation, "dashboard": step_dashboard,
             "waze": step_waze, "rename-commute": step_rename_commute, "areas": step_areas, "views": step_views, "garage-hero": step_garage_hero, "garage-lights": step_garage_lights, "swipe-style": step_swipe_style, "swipe-tidy": step_swipe_tidy, "laundry-group": step_laundry_group, "lights-page": step_lights_page, "odometer-miles": step_odometer_miles, "swipe-pages": step_swipe_pages, "renames": step_renames, "blinds": step_blinds, "blinds-security": step_blinds_security, "sony": step_sony, "outfit-stale": step_outfit_stale, "security-swipe": step_security_swipe, "roborock-maps": step_roborock_maps, "cleaning-media": step_cleaning_media, "remove-office": step_remove_office, "shortcuts": step_shortcuts, "header-polish": step_header_polish, "climate-page": step_climate_page, "climate-polish": step_climate_polish, "climate-compact": step_climate_compact, "occupancy-pills": step_occupancy_pills, "cleaning-premium": step_cleaning_premium, "cleaning-rooms": step_cleaning_rooms, "cleaning-deck": step_cleaning_deck, "map-crop": step_map_crop, "backup-folders": step_backup_folders, "firmware": step_firmware, "firmware-status": step_firmware_status, "reolink-reload": step_reolink_reload,
             "remove-deck-cam-light": step_remove_deck_cam_light, "disable-tuya-local": step_disable_tuya_local,
             "abode-dashboard": step_abode_dashboard, "iota-snapshot": step_iota_snapshot, "mower-page": step_mower_page, "presence-setup": step_presence_setup,
             "presence-dashboard": step_presence_dashboard, "presence-automations": step_presence_automations,
             "basement-laundry": step_basement_laundry, "ha-backup": step_ha_backup, "basement-final": step_basement_final, "addon-update": step_addon_update, "tv-fireplace": step_tv_fireplace,
             "test-on": step_test_on, "test-off": step_test_off, "run": step_run}
    try:
        if step == "backup":
            step_backup()
        elif step in steps:
            print(("APPLYING" if apply else "DRY RUN (nothing sent)") + f": {step}")
            steps[step](apply)
        else:
            sys.exit(__doc__)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"ERROR: {type(e).__name__}: {h.scrub(e)}")
