"""RETIRED (2026-09-27): builds the v1 prototype (template.v1.html -> home-panel.v1.html).
home-panel.html is now hand-maintained to mirror the live dashboard; this script no longer writes it.

Builds the dashboard prototype from the sanitized inventory.

Reads inventory/ha_inventory.json (local file only; never contacts Home Assistant),
keeps dashboard-relevant enabled entities, drops anything flagged sensitive, attaches
review flags (uncertain names, duplicates, unassigned) and demo values, and injects
the result into template.html -> home-panel.html.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INV = json.loads((ROOT.parent / "inventory" / "ha_inventory.json").read_text(encoding="utf-8"))

SKIP_DOMAINS = {"update", "tts", "stt", "conversation", "ai_task", "notify", "event", "image",
                "input_text", "script", "todo"}

# Review flags. Text describes what the inventory shows; nothing is renamed.
FLAGS = {
    "camera.front_gate_fluent": "Name says “Homesite Back Deck”, area is Front Gate",
    "camera.homesite_back_deck_fluent": "ID says back deck, name says “Driveway”, area is Driveway",
    "camera.homesite_driveway_fluent": "ID says driveway, name says “Front Gate”, area is Driveway",
    "camera.front_yard_fluent": "Possible duplicate of camera.front_yard_fluent_2 (this one is offline)",
    "camera.front_yard_fluent_2": "Possible duplicate of camera.front_yard_fluent; has no area",
    "media_player.sony_tv": "One of 4 Sony TV players; may be the same TV",
    "media_player.sony_tv_2": "One of 4 Sony TV players; may be the same TV",
    "media_player.sony_tv_3": "One of 4 Sony TV players; may be the same TV",
    "media_player.great_room_sony_tv": "Same TV also appears as sony_tv, sony_tv_2 and sony_tv_3; those are grouped here",
    "remote.sony_tv": "Two remotes for what may be one TV",
    "remote.great_room_sony_tv": "Two remotes for what may be one TV",
    "switch.great_room_smart_bridge_unassigned_smart_away": "Purpose unclear (Lutron bridge setting)",
}
for suffix in ("motion", "person", "vehicle"):
    FLAGS[f"binary_sensor.front_gate_{suffix}"] = "Name says “Homesite Back Deck”, area is Front Gate"
    FLAGS[f"binary_sensor.homesite_back_deck_{suffix}"] = "ID says back deck, name says “Homesite Driveway”"
    FLAGS[f"binary_sensor.homesite_driveway_{suffix}"] = "ID says driveway, name says “Front Gate”"
    FLAGS[f"binary_sensor.front_yard_{suffix}_2"] = f"Possible duplicate of binary_sensor.front_yard_{suffix}; has no area"

# Illustrative values only (the inventory deliberately stores no states).
DEMO = {
    "sensor.bedroom_sensor_temperature": "68.4",
    "sensor.great_room_thermostat_temperature": "69.8",
    "sensor.living_room_thermostat_temperature": "72.1",
    "sensor.great_room_thermostat_humidity": "41",
    "sensor.living_room_thermostat_humidity": "44",
    "sensor.polestar_2_battery_charge_level": "78",
    "sensor.polestar_2_range": "204",
    "sensor.polestar_2_charging_status": "Idle",
    "sensor.polestar_2_charger_connection_status": "Connected",
    "sensor.polestar_2_estimate_full_charge_range": "262",
    "sensor.polestar_2_charging_power": "0",
    "sensor.polestar_2_days_to_service": "143",
    "sensor.living_room_vacuum_battery": "100",
    "sensor.front_door_battery": "100",
    "sensor.epson_et_2850_series": "Idle",
    "sensor.epson_et_2850_series_black_ink": "64",
    "sensor.epson_et_2850_series_cyan_ink": "38",
    "sensor.epson_et_2850_series_magenta_ink": "52",
    "sensor.epson_et_2850_series_yellow_ink": "17",
}


def demo_value(e):
    eid, dom, c = e["entity_id"], e["domain"], e["capabilities"]
    if e["availability"] != "available":
        return e["availability"]
    if eid in DEMO:
        return DEMO[eid]
    dc = c.get("device_class")
    if dom == "binary_sensor":
        return "on" if dc == "occupancy" and "bedroom" not in eid else "off"
    if dom == "light":
        return "on" if any(k in eid for k in ("kitchen_island", "living_room_main", "backyard_indicator")) else "off"
    if dom == "switch":
        return "off"
    if dom == "media_player":
        return "playing" if eid in ("media_player.soundbar", "media_player.great_room_sony_tv") else "idle"
    if dom == "vacuum":
        return "docked"
    if dom == "climate":
        return (c.get("hvac_modes") or ["off"])[0]
    if dom == "lock":
        return "locked"
    if dom == "sensor":
        return {"battery": "85", "temperature": "70", "humidity": "45"}.get(dc, "—")
    return e["availability"]


def tidy(name):
    """Prototype-only display label. Home Assistant names are not changed; the
    original name stays in the row and is shown in the Inventory view."""
    n = re.sub(r"\s+Fluent$", "", name.strip())  # "Fluent" is a Reolink stream-quality label
    words = []
    for w in n.split():
        if w.lower() == "tv":
            w = "TV"
        elif w[:1].islower():
            w = w[:1].upper() + w[1:]
        words.append(w)
    return " ".join(words)


# One physical Sony TV exposed by several integrations. The Bravia entity is kept as the
# primary card; the others are hidden on Home and Rooms but listed in Inventory.
SONY_PRIMARY = "media_player.great_room_sony_tv"
SONY_DUPES = {"media_player.sony_tv", "media_player.sony_tv_2", "media_player.sony_tv_3", "remote.sony_tv"}


def keep_diagnostic(e):
    """Battery sensors are often marked diagnostic but belong on device cards."""
    return e["role"] == "diagnostic" and e["capabilities"].get("device_class") == "battery"


rows = []
for e in INV["entities"]:
    if e["sensitive"] or e["hidden"] or e["domain"] in SKIP_DOMAINS or e["integration"] in ("mobile_app", "backup"):
        continue
    if e["availability"].startswith("disabled") or (e["role"] not in ("control", "status", "camera") and not keep_diagnostic(e)):
        continue
    if e["domain"] in ("weather",) and "thermostat" in e["entity_id"]:
        continue  # ecobee weather duplicates; keep forecast_home
    c = e["capabilities"]
    rows.append({
        "id": e["entity_id"], "name": e["display_name"].strip(), "label": tidy(e["display_name"]),
        "dup": e["entity_id"] in SONY_DUPES, "domain": e["domain"],
        "room": "Unassigned" if e["room"] == "(no area)" else e["room"],
        "role": e["role"], "device": e["device"], "model": e["model"],
        "integration": e["integration"], "features": e["features"],
        "caps": {k: c[k] for k in ("device_class", "unit_of_measurement", "supported_color_modes",
                                   "hvac_modes", "fan_modes", "preset_modes", "min_temp",
                                   "max_temp", "fan_speed_list", "source_list") if k in c},
        "avail": e["availability"], "flag": FLAGS.get(e["entity_id"], ""), "demo": demo_value(e),
    })

meta = INV["meta"]
disabled_cams = sum(1 for e in INV["entities"] if e["domain"] == "camera" and e["availability"].startswith("disabled"))
payload = {
    "meta": {"generated": meta["generated_utc"], "ha_version": meta["ha_version"],
             "counts": meta["counts"], "disabled_cameras": disabled_cams,
             "sensitive_hidden": sum(1 for e in INV["entities"] if e["sensitive"]),
             "areas": [a["name"] for a in INV["areas"]]},
    "entities": rows,
}
html = (ROOT / "template.v1.html").read_text(encoding="utf-8").replace(
    "__DATA__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
(ROOT / "home-panel.v1.html").write_text(html, encoding="utf-8")
print(f"{len(rows)} entities -> home-panel.v1.html ({len(html)//1024} KB)")
