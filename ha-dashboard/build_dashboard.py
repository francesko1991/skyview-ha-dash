"""RETIRED - used once, on 2026-09-26, to create the first draft. Do not run it for changes.

The live dashboard (url_path `home-panel`) is now the master copy. Edit it in Home Assistant
and pull it with `scripts/run-ha.ps1 --pull home-panel` (see INSTALL.md > Revising).
Running this script overwrites home_panel_dashboard.yaml and themes/home_panel.yaml with a
fresh generated draft that knows nothing about live edits, so it refuses to run unless
started with --regenerate-draft.

Original description: generates the Home Panel dashboard draft from the saved, sanitized inventory.

Outputs (in this folder):
  home_panel_dashboard.yaml   paste into a NEW dashboard's raw configuration editor
  themes/home_panel.yaml      optional theme holding every colour/shape token

Reads only ../inventory/ha_inventory.json (never contacts Home Assistant). No third-party
packages needed. The YAML files are the editable deliverables; re-running this script
overwrites them, so either edit the YAML directly or change this script and regenerate.
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
INV = json.loads((HERE.parent / "inventory" / "ha_inventory.json").read_text(encoding="utf-8"))
ROWS = {e["entity_id"]: e for e in INV["entities"]}
SNAPSHOT = INV["meta"]["generated_utc"][:10]
DASH = "home-panel"          # suggested URL path of the new dashboard
THEME = "Home Panel"

# ---------------------------------------------------------------- entity selection
SKIP_DOMAINS = {"update", "tts", "stt", "conversation", "ai_task", "notify", "event", "image",
                "input_text", "script", "todo"}
SONY_PRIMARY = "media_player.great_room_sony_tv"
SONY_DUPES = {"media_player.sony_tv", "media_player.sony_tv_2", "media_player.sony_tv_3", "remote.sony_tv"}

# Mixed-up or duplicate cameras: flagged, never renamed to a guessed location.
CAMERA_FLAGS = {
    "camera.front_gate_fluent": "Name says “Homesite Back Deck”, area is Front Gate",
    "camera.homesite_back_deck_fluent": "ID says back deck, name says “Driveway”, area is Driveway",
    "camera.homesite_driveway_fluent": "ID says driveway, name says “Front Gate”, area is Driveway",
    "camera.front_yard_fluent": "Possible duplicate of camera.front_yard_fluent_2",
    "camera.front_yard_fluent_2": "Possible duplicate of camera.front_yard_fluent; no area",
}


def usable(e):
    if e["sensitive"] or e["hidden"] or e["domain"] in SKIP_DOMAINS or e["domain"] == "zone":
        return False
    if e["integration"] in ("mobile_app", "backup") or e["entity_id"] in SONY_DUPES:
        return False
    if e["availability"].startswith("disabled"):
        return False
    if e["role"] in ("control", "status", "camera"):
        return not (e["domain"] == "weather" and "thermostat" in e["entity_id"])
    return e["role"] == "diagnostic" and e["capabilities"].get("device_class") == "battery"


ENTS = [e for e in INV["entities"] if usable(e)]
room_of = lambda e: "Unassigned" if e["room"] == "(no area)" else e["room"]
AREAS = [a["name"] for a in INV["areas"]]
CAMERAS = sorted([e for e in ENTS if e["domain"] == "camera"], key=lambda e: (room_of(e) == "Unassigned", room_of(e), e["entity_id"]))
LIGHTS = [e for e in ENTS if e["domain"] in ("light", "switch") and e["role"] == "control"
          and not re.search(r"status_led|indicator|smart_away|dust_emptying", e["entity_id"])]
PLAYERS = [e for e in ENTS if e["domain"] == "media_player"]
UNASSIGNED_CONTROLS = [e["entity_id"] for e in ENTS if room_of(e) == "Unassigned" and e["role"] == "control"]


def tidy(name):
    """Display label only (the HA entity name is unchanged). Drops the Reolink 'Fluent'
    stream label and capitalises words; flagged cameras keep their HA wording."""
    n = re.sub(r"\s+Fluent$", "", name.strip())
    return " ".join("TV" if w.lower() == "tv" else (w[:1].upper() + w[1:] if w[:1].islower() else w) for w in n.split())


def label(eid):
    return tidy(ROWS[eid]["display_name"])


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


# ---------------------------------------------------------------- card builders
def heading(text, icon=None, style="title", badges=None):
    c = {"type": "heading", "heading": text, "heading_style": style}
    if icon:
        c["icon"] = icon
    if badges:
        c["badges"] = badges
    return c


def md(content, **kw):
    return {"type": "markdown", "content": content, **kw}


def perform(action, eid, confirm=None):
    a = {"action": "perform-action", "perform_action": action, "target": {"entity_id": eid}}
    if confirm:
        a["confirmation"] = {"text": confirm}
    return a


def tile(eid, name=None, features=None, **kw):
    e = ROWS[eid]
    c = {"type": "tile", "entity": eid}
    nm = name or label(eid)
    if nm != e["display_name"].strip():
        c["name"] = nm
    d = e["domain"]
    if features is None:
        features = default_features(e)
    if features:
        c["features"] = features
        c["features_position"] = "bottom"
    if d == "button":
        c["tap_action"] = perform("button.press", eid)
        c["icon_tap_action"] = perform("button.press", eid)
    elif d == "siren":
        # Sounding a siren always asks first; tap opens details.
        c["tap_action"] = {"action": "more-info"}
        c["icon_tap_action"] = perform("siren.turn_on", eid, f"Sound the {label(eid)}?")
    elif d == "remote":
        c["icon_tap_action"] = {"action": "more-info"}
    c.update(kw)
    return c


def default_features(e):
    d, caps, feats = e["domain"], e["capabilities"], e["features"]
    if d == "light" and any(m != "onoff" for m in caps.get("supported_color_modes", [])):
        return [{"type": "light-brightness"}]
    if d == "climate":
        return [{"type": "target-temperature"},
                {"type": "climate-hvac-modes", "style": "icons", "hvac_modes": caps.get("hvac_modes", [])},
                {"type": "climate-preset-modes", "style": "dropdown", "preset_modes": caps.get("preset_modes", [])}]
    if d == "vacuum":
        cmds = [c for c, f in (("start_pause", "start"), ("return_home", "return_home"), ("locate", "locate")) if f in feats]
        out = [{"type": "vacuum-commands", "commands": cmds}]
        if "fan_speed" in feats:
            out.append({"type": "vacuum-fan-speed"})
        return out
    if d == "media_player":
        out = [{"type": "media-player-playback", "controls": ["media_play_pause"]}]
        if "volume_set" in feats:
            out.append({"type": "media-player-volume-slider"})
        if "select_source" in feats:
            out.append({"type": "media-player-source"})
        return out
    return []


def camera_card(eid, span=12):
    e, flag = ROWS[eid], CAMERA_FLAGS.get(eid)
    name = label(eid) + (" · check name" if flag else "") + (" · no area" if room_of(e) == "Unassigned" and not flag else "")
    return {"type": "picture-entity", "entity": eid, "name": name, "camera_view": "auto",
            "show_name": True, "show_state": True, "fit_mode": "cover", "aspect_ratio": "16:9",
            "tap_action": {"action": "more-info"}, "grid_options": {"columns": span}}


def state_is(eid, states):
    return {"condition": "state", "entity": eid, "state": states}


def state_not(eid, states):
    return {"condition": "state", "entity": eid, "state_not": states}


BAD = ["unavailable", "unknown"]


def list_jinja(ids):
    return "[" + ", ".join(f"'{i}'" for i in ids) + "]"


# ---------------------------------------------------------------- Home view
def attention_chips():
    cams = [e["entity_id"] for e in CAMERAS]
    lights = [e["entity_id"] for e in LIGHTS]
    return {
        "type": "custom:mushroom-chips-card",
        "alignment": "start",
        "chips": [
            {"type": "template", "entity": "lock.front_door",
             "icon": "{{ 'mdi:lock-question' if states('lock.front_door') in ['unknown','unavailable'] else 'mdi:lock' }}",
             "icon_color": "{{ 'amber' if states('lock.front_door') in ['unknown','unavailable','jammed','open'] else 'teal' }}",
             "content": "Lock: {{ state_translated('lock.front_door') }}",
             "tap_action": {"action": "more-info"}},
            {"type": "template", "icon": "mdi:cctv-off",
             "icon_color": f"{{{{ 'red' if {list_jinja(cams)} | map('states') | select('in', ['unavailable','unknown']) | list | count else 'teal' }}}}",
             "content": f"{{{{ {list_jinja(cams)} | map('states') | select('in', ['unavailable','unknown']) | list | count }}}} of {len(cams)} cameras offline",
             "entity_id": cams,
             "tap_action": {"action": "navigate", "navigation_path": f"/{DASH}/cameras"}},
            {"type": "template", "icon": "mdi:tag-search", "icon_color": "amber",
             "content": f"{len(CAMERA_FLAGS)} camera names to check",
             "tap_action": {"action": "navigate", "navigation_path": f"/{DASH}/review"}},
            {"type": "template", "icon": "mdi:lightbulb-off-outline",
             "icon_color": f"{{{{ 'red' if {list_jinja(lights)} | map('states') | select('eq', 'unavailable') | list | count else 'teal' }}}}",
             "content": f"{{{{ {list_jinja(lights)} | map('states') | select('eq', 'unavailable') | list | count }}}} lights/plugs unavailable",
             "entity_id": lights,
             "tap_action": {"action": "navigate", "navigation_path": f"/{DASH}/review"}},
            {"type": "template", "icon": "mdi:map-marker-question-outline", "icon_color": "grey",
             "content": f"{{{{ {list_jinja(UNASSIGNED_CONTROLS)} | reject('area_id') | list | count }}}} controls with no area",
             "tap_action": {"action": "navigate", "navigation_path": f"/{DASH}/review"}},
        ],
        "grid_options": {"columns": "full"},
    }


def lock_section():
    lock = "lock.front_door"
    return {"type": "grid", "cards": [
        heading("Security", "mdi:shield-home"),
        tile(lock, features=[]),
        md("**Lock state is not confirmed.** Home Assistant reports this lock as "
           "*{{ state_translated('lock.front_door') }}*, so this dashboard can't show whether the door is locked. "
           "The door sensor is *{{ state_translated('binary_sensor.front_door_door') }}*.",
           visibility=[state_is(lock, BAD)]),
        {"type": "button", "name": "Lock…", "icon": "mdi:lock", "show_state": False,
         "tap_action": perform("lock.lock", lock, "Send a lock command to the front door? Its current state is not confirmed."),
         "grid_options": {"columns": 6, "rows": 1}},
        {"type": "button", "name": "Unlock…", "icon": "mdi:lock-open-variant", "show_state": False,
         "tap_action": perform("lock.unlock", lock, "Unlock the front door?"),
         "grid_options": {"columns": 6, "rows": 1}},
        tile("binary_sensor.front_door_door", name="Front Door Sensor"),
        tile("sensor.front_door_battery"),
    ]}


def cleaning_section():
    v = "vacuum.living_room_vacuum"
    return {"type": "grid", "cards": [
        heading("Cleaning", "mdi:robot-vacuum"),
        tile(v, grid_options={"columns": 12}),
        tile("sensor.living_room_vacuum_battery"),
        tile("button.living_room_vacuum_full_cleaning"),
    ]}


def cameras_home_section():
    online = [e["entity_id"] for e in CAMERAS if e["availability"] == "available"]
    return {"type": "grid", "column_span": 2, "cards": [
        heading("Cameras", "mdi:cctv", badges=[{"type": "button", "text": f"All {len(CAMERAS)} cameras", "icon": "mdi:arrow-right",
                                                  "tap_action": {"action": "navigate", "navigation_path": f"/{DASH}/cameras"}}]),
        *[camera_card(eid, span=6) for eid in online],
    ]}


def climate_section():
    return {"type": "grid", "column_span": 2, "cards": [
        heading("Climate", "mdi:thermostat"),
        tile("climate.great_room_thermostat", name="Great Room Thermostat", grid_options={"columns": 6}),
        tile("climate.living_room_thermostat", name="Living Room Thermostat", grid_options={"columns": 6}),
        tile("sensor.great_room_thermostat_humidity", name="Great Room Humidity"),
        tile("sensor.living_room_thermostat_humidity", name="Living Room Humidity"),
        tile("sensor.bedroom_sensor_temperature", name="Bedroom Temperature"),
        tile("binary_sensor.bedroom_sensor_occupancy", name="Bedroom Occupancy"),
        tile("binary_sensor.living_room_thermostat_occupancy", name="Living Room Occupancy"),
    ]}


def lights_section():
    by_room = {}
    for e in LIGHTS:
        by_room.setdefault(room_of(e), []).append(e)
    cards = [heading("Lights and plugs", "mdi:lightbulb-group")]
    for r in sorted(by_room, key=lambda x: (x == "Unassigned", x)):
        cards.append(heading(r if r != "Unassigned" else "No area yet", style="subtitle"))
        for e in by_room[r]:
            # Shown here while working; moved to "Unavailable right now" when it isn't.
            cards.append(tile(e["entity_id"], visibility=[state_not(e["entity_id"], "unavailable")]))
    ids = [e["entity_id"] for e in LIGHTS]
    cards.append(heading("Unavailable right now", "mdi:alert-circle-outline", style="subtitle",
                         ) | {"visibility": [{"condition": "or", "conditions": [state_is(i, "unavailable") for i in ids]}]})
    for i in ids:
        cards.append(tile(i, visibility=[state_is(i, "unavailable")]))
    cards.append({"type": "button", "name": "Turn all lights off…", "icon": "mdi:lightbulb-group-off", "show_state": False,
                  "tap_action": {"action": "perform-action", "perform_action": "light.turn_off",
                                 "target": {"entity_id": [e["entity_id"] for e in LIGHTS if e["domain"] == "light"]},
                                 "confirmation": {"text": "Turn off all lights on this dashboard?"}},
                  "grid_options": {"columns": 12, "rows": 1}})
    return {"type": "grid", "cards": cards}


def media_section():
    players = sorted(PLAYERS, key=lambda e: label(e["entity_id"]))
    cards = [heading("Media", "mdi:speaker-multiple")]
    for e in players:
        eid = e["entity_id"]
        nm = label(eid) + (" · 4 entities, shown once" if eid == SONY_PRIMARY else "")
        cards.append(tile(eid, name=nm))
    return {"type": "grid", "cards": cards}


def garage_section():
    ids = ["sensor.polestar_2_battery_charge_level", "sensor.polestar_2_range", "sensor.polestar_2_charging_status",
           "sensor.polestar_2_charger_connection_status", "sensor.polestar_2_charging_power",
           "sensor.polestar_2_estimate_full_charge_range", "sensor.polestar_2_days_to_service", "sensor.polestar_2_service_warning"]
    return {"type": "grid", "cards": [heading("Garage · Polestar 2", "mdi:car-electric"),
                                      *[tile(i, name=label(i).replace("Polestar 2 ", "")) for i in ids if i in ROWS]]}


def office_section():
    base = "sensor.epson_et_2850_series"
    return {"type": "grid", "cards": [heading("Office · Epson ET-2850", "mdi:printer"), tile(base, name="Printer status"),
                                      *[tile(f"{base}_{c}_ink", name=f"{c.title()} ink") for c in ("black", "cyan", "magenta", "yellow")]]}


def home_view():
    return {"title": "Home", "path": "home", "icon": "mdi:home", "type": "sections", "max_columns": 3,
            "theme": THEME, "dense_section_placement": True,
            "sections": [
                {"type": "grid", "column_span": 3, "cards": [
                    {"type": "clock", "clock_style": "digital", "clock_size": "large", "no_background": True,
                     "grid_options": {"columns": "full"}},
                    attention_chips()]},
                lock_section(), cleaning_section(),
                cameras_home_section(), climate_section(),
                lights_section(), media_section(), garage_section(), office_section(),
            ]}


# ---------------------------------------------------------------- Rooms
GROUPS = [("Climate and security", "mdi:home-thermometer", ("climate", "lock")),
          ("Lights and plugs", "mdi:lightbulb-group", ("light", "switch")),
          ("Media", "mdi:speaker", ("media_player", "remote")),
          ("Cleaning", "mdi:robot-vacuum", ("vacuum",)),
          ("Cameras", "mdi:cctv", ("camera",)),
          ("Camera controls", "mdi:camera-control", ("button", "siren", "number", "select")),
          ("Sensors", "mdi:gauge", ("sensor", "binary_sensor", "weather", "sun"))]


def room_entities(r):
    return [e for e in ENTS if room_of(e) == r]


def rooms_view():
    rooms = [r for r in AREAS if room_entities(r)] + ["Unassigned"]
    empty = [r for r in AREAS if not room_entities(r)]
    cards = [heading("Rooms", "mdi:floor-plan")]
    for r in rooms:
        cards.append({"type": "button", "name": r if r != "Unassigned" else "No area yet",
                      "icon": "mdi:map-marker-question-outline" if r == "Unassigned" else "mdi:door",
                      "show_state": False, "tap_action": {"action": "navigate", "navigation_path": f"/{DASH}/room-{slug(r)}"},
                      "grid_options": {"columns": 6, "rows": 2}})
    if empty:
        cards.append(md("No dashboard entities yet: " + ", ".join(empty) + "."))
    return {"title": "Rooms", "path": "rooms", "icon": "mdi:floor-plan", "type": "sections", "max_columns": 2,
            "theme": THEME, "sections": [{"type": "grid", "column_span": 2, "cards": cards}]}


def room_subview(r):
    ents = room_entities(r)
    sections = []
    if r == "Unassigned":
        sections.append({"type": "grid", "column_span": 2, "cards": [md(
            "These entities have **no area** in Home Assistant. They are listed as found; "
            "assign areas in Home Assistant and they will move to their rooms.")]})
    for title, icon, domains in GROUPS:
        group = sorted([e for e in ents if e["domain"] in domains], key=lambda e: (e["domain"], label(e["entity_id"])))
        if not group:
            continue
        if title == "Cameras":
            cards = [camera_card(e["entity_id"]) for e in group]
        else:
            cards = [tile(e["entity_id"], **({"grid_options": {"columns": 12}} if e["domain"] in ("climate", "vacuum", "media_player") else {}))
                     for e in group]
        sections.append({"type": "grid", "cards": [heading(title, icon), *cards]})
    return {"title": r if r != "Unassigned" else "No area yet", "path": f"room-{slug(r)}", "subview": True,
            "back_path": f"/{DASH}/rooms", "type": "sections", "max_columns": 2, "theme": THEME, "sections": sections}


# ---------------------------------------------------------------- Cameras + Review
def cameras_view():
    by_room = {}
    for e in CAMERAS:
        by_room.setdefault(room_of(e), []).append(e["entity_id"])
    notes = "\n".join(f"| {label(i)} | `{i}` | {room_of(ROWS[i])} | {CAMERA_FLAGS[i]} |" for i in CAMERA_FLAGS)
    sections = [{"type": "grid", "column_span": 2, "cards": [
        heading("Cameras", "mdi:cctv"),
        md("Each card shows the camera's **live state** from Home Assistant (a snapshot, or *Unavailable*). "
           "Cards marked **check name** have names that disagree with their entity ID or area; they are not "
           "renamed or moved here.\n\n| Shown as | Entity | Area | Why it's flagged |\n|---|---|---|---|\n" + notes)]}]
    for r in sorted(by_room, key=lambda x: (x == "Unassigned", x)):
        sections.append({"type": "grid", "cards": [heading(r if r != "Unassigned" else "No area yet", "mdi:map-marker", style="subtitle"),
                                                   *[camera_card(i) for i in by_room[r]]]})
    return {"title": "Cameras", "path": "cameras", "icon": "mdi:cctv", "type": "sections", "max_columns": 3,
            "theme": THEME, "sections": sections}


def review_view():
    flagged_rows = []
    for eid, why in list(CAMERA_FLAGS.items()) + [
            (f"binary_sensor.{p}_{s}", w) for p, w in (("front_gate", "Name says “Homesite Back Deck”"),
                                                          ("homesite_back_deck", "ID says back deck, name says “Homesite Driveway”"),
                                                          ("homesite_driveway", "ID says driveway, name says “Front Gate”"))
            for s in ("motion", "person", "vehicle")] + [
            (SONY_PRIMARY, "Same TV also appears as media_player.sony_tv, sony_tv_2, sony_tv_3 and remote.sony_tv (hidden here)"),
            ("switch.great_room_smart_bridge_unassigned_smart_away", "Purpose unclear (Lutron bridge setting)")]:
        if eid in ROWS:
            flagged_rows.append(f"| `{eid}` | {ROWS[eid]['display_name']} | {why} |")
    unassigned = "\n".join(f"| `{i}` | {ROWS[i]['display_name']} |" for i in UNASSIGNED_CONTROLS)
    return {"title": "Review", "path": "review", "subview": True, "back_path": f"/{DASH}/home", "type": "sections",
            "max_columns": 2, "theme": THEME, "sections": [
                {"type": "grid", "column_span": 2, "cards": [
                    heading("Needs review", "mdi:clipboard-check-outline"),
                    md(f"Snapshot from the inventory on {SNAPSHOT}. Nothing here changes Home Assistant.\n\n"
                       "### Names to check\n| Entity | Name in Home Assistant | Why |\n|---|---|---|\n" + "\n".join(flagged_rows)),
                    md("### Controls with no area\n| Entity | Name in Home Assistant |\n|---|---|\n" + unassigned),
                ]}]}


def dashboard():
    views = [home_view(), rooms_view(), cameras_view(), review_view()]
    views += [room_subview(r) for r in [a for a in AREAS if room_entities(a)] + ["Unassigned"]]
    return {"title": "Home Panel", "views": views}


# ---------------------------------------------------------------- theme (all colours live here)
THEME_TOKENS = {
    "light": {"hp-ground": "#e9edea", "hp-surface": "#f8faf8", "hp-surface-2": "#dfe5e1", "hp-line": "#cbd4ce",
              "hp-ink": "#18211d", "hp-muted": "#56635d", "hp-glow": "#e39b2d", "hp-teal": "#1c6b62",
              "hp-bad": "#b3372c", "hp-warn": "#9a6a00", "hp-ok": "#2d7a4b",
              "hp-rgb-glow": "227, 155, 45", "hp-rgb-teal": "28, 107, 98", "hp-rgb-bad": "179, 55, 44", "hp-rgb-warn": "154, 106, 0",
              "hp-shadow": "0 1px 2px rgba(20,35,28,.06), 0 4px 14px rgba(20,35,28,.06)"},
    "dark": {"hp-ground": "#101513", "hp-surface": "#18201c", "hp-surface-2": "#222c27", "hp-line": "#2f3a34",
             "hp-ink": "#e4ebe7", "hp-muted": "#9aa8a1", "hp-glow": "#f0ab45", "hp-teal": "#5cb8aa",
             "hp-bad": "#ef7d70", "hp-warn": "#e7b94c", "hp-ok": "#6cc58f",
             "hp-rgb-glow": "240, 171, 69", "hp-rgb-teal": "92, 184, 170", "hp-rgb-bad": "239, 125, 112", "hp-rgb-warn": "231, 185, 76",
             "hp-shadow": "none"},
}
THEME_MAP = {  # Home Assistant / Mushroom variable -> design token
    "lovelace-background": "var(--hp-ground)", "primary-background-color": "var(--hp-ground)",
    "card-background-color": "var(--hp-surface)", "ha-card-background": "var(--hp-surface)",
    "secondary-background-color": "var(--hp-surface-2)", "divider-color": "var(--hp-line)",
    "ha-card-border-color": "var(--hp-line)", "ha-card-border-radius": "14px", "ha-card-box-shadow": "var(--hp-shadow)",
    "primary-text-color": "var(--hp-ink)", "secondary-text-color": "var(--hp-muted)",
    "primary-color": "var(--hp-teal)", "accent-color": "var(--hp-glow)",
    "error-color": "var(--hp-bad)", "warning-color": "var(--hp-warn)", "success-color": "var(--hp-ok)",
    "state-light-active-color": "var(--hp-glow)", "state-switch-active-color": "var(--hp-glow)",
    "state-climate-heat-color": "var(--hp-glow)", "state-climate-cool-color": "var(--hp-teal)",
    "state-media_player-active-color": "var(--hp-teal)", "state-vacuum-active-color": "var(--hp-teal)",
    "state-lock-locked-color": "var(--hp-ok)", "state-lock-unlocked-color": "var(--hp-bad)",
    "state-lock-jammed-color": "var(--hp-bad)", "state-lock-open-color": "var(--hp-bad)",
    "mush-chip-background": "var(--hp-surface)", "mush-chip-border-color": "var(--hp-line)",
    "mush-chip-border-width": "1px", "mush-chip-border-radius": "999px",
    "mush-rgb-amber": "var(--hp-rgb-warn)", "mush-rgb-red": "var(--hp-rgb-bad)", "mush-rgb-teal": "var(--hp-rgb-teal)",
}


def theme():
    return {THEME: {"modes": {m: {**tok, **THEME_MAP} for m, tok in THEME_TOKENS.items()}}}


# ---------------------------------------------------------------- minimal YAML emitter
RESERVED = {"yes", "no", "on", "off", "true", "false", "null", "y", "n", "~"}
SAFE = re.compile(r"^[A-Za-z_/][A-Za-z0-9_ .\-/…·]*$")


def scalar(v):
    if v is True:
        return "true"
    if v is False:
        return "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if SAFE.match(s) and s.lower() not in RESERVED and not s.endswith(" ") and ": " not in s:
        return s
    return json.dumps(s, ensure_ascii=False)


def emit(node, indent=0, out=None):
    out = [] if out is None else out
    pad = "  " * indent
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and "\n" in v:
                out.append(f"{pad}{k}: |-")
                out.extend(f"{pad}  {line}" if line else "" for line in v.split("\n"))
            elif isinstance(v, (dict, list)) and v:
                out.append(f"{pad}{k}:")
                emit(v, indent + (1 if isinstance(v, dict) else 1), out)
            elif isinstance(v, (dict, list)):
                out.append(f"{pad}{k}: {'{}' if isinstance(v, dict) else '[]'}")
            else:
                out.append(f"{pad}{k}: {scalar(v)}")
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, dict) and item:
                sub = emit(item, indent + 1, [])
                out.append(f"{pad}- {sub[0].lstrip()}")
                out.extend(sub[1:])
            elif isinstance(item, list):
                out.append(f"{pad}-")
                emit(item, indent + 1, out)
            else:
                out.append(f"{pad}- {scalar(item)}")
    return out


if __name__ == "__main__":
    import sys
    if "--regenerate-draft" not in sys.argv:
        sys.exit("build_dashboard.py is retired: the live 'home-panel' dashboard is the master copy.\n"
                 "Pull it instead:  scripts/run-ha.ps1 --pull home-panel   (see INSTALL.md > Revising)\n"
                 "To overwrite the draft files anyway, run:  python build_dashboard.py --regenerate-draft")
    header = [
        "# Home Panel dashboard — DRAFT generated from the sanitized inventory of " + SNAPSHOT,
        "# Paste into a NEW dashboard: Settings > Dashboards > Add dashboard > New dashboard from scratch,",
        "# then open it > pencil (Edit) > three-dot menu > Raw configuration editor. See INSTALL.md.",
        "# Needs: Mushroom 5.x (custom:mushroom-chips-card). Optional: themes/home_panel.yaml.",
        "# Colours are NOT set here; they live in themes/home_panel.yaml.",
        "",
    ]
    (HERE / "home_panel_dashboard.yaml").write_text("\n".join(header + emit(dashboard())) + "\n", encoding="utf-8")
    (HERE / "themes").mkdir(exist_ok=True)
    theme_header = [
        "# Home Panel theme — every colour and shape used by the dashboard.",
        "# Edit the hp-* tokens to restyle; the lower block maps them onto Home Assistant and Mushroom variables.",
        "# Install: copy to <config>/themes/ and make sure configuration.yaml has",
        "#   frontend:",
        "#     themes: !include_dir_merge_named themes",
        "",
    ]
    (HERE / "themes" / "home_panel.yaml").write_text("\n".join(theme_header + emit(theme())) + "\n", encoding="utf-8")
    d = dashboard()
    print(f"views: {len(d['views'])}  (4 main + {len(d['views']) - 4} room subviews)")
