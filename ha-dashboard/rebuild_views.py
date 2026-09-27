"""Rebuilds the untouched views (Cameras, Review, room-*) from the current inventory.

Home and Rooms are NOT produced here (they are edited live). Output: outfit/payloads/views.json,
applied with `run-apply.ps1 views --apply` after a dry run. Reuses build_dashboard.py's functions
(its own main stays retired) with camera cards matching the live Home section: clean names,
green online dot, hidden while unavailable.
"""
import json
from pathlib import Path

import build_dashboard as bd

HERE = Path(__file__).resolve().parent
OUT = HERE / "outfit" / "payloads" / "views.json"

# Names settled on 2026-09-26 (the cameras' own labels and pictures agree with these).
CAMERA_NAMES = {
    "camera.homesite_back_deck_fluent": "Driveway",
    "camera.front_gate_fluent": "Back Deck",
    "camera.homesite_driveway_fluent": "Front Gate",
    "camera.front_yard_fluent": "Front Yard",
    "camera.backyard": "Backyard",
}
CAMERA_IMAGE = {"camera.front_yard_fluent": "camera.front_yard_snapshots_fluent_2"}
ONLINE_DOT = """ha-card::after {
  content: "";
  position: absolute;
  right: 14px;
  bottom: 14px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--success-color, #4caf50);
  box-shadow: 0 0 0 3px rgba(0, 0, 0, 0.45);
  z-index: 3;
}
"""


def camera_card(eid, span=12):
    c = {"type": "picture-entity", "entity": eid, "name": CAMERA_NAMES.get(eid, bd.label(eid))}
    if eid in CAMERA_IMAGE:
        c["camera_image"] = CAMERA_IMAGE[eid]
    c.update({"camera_view": "auto", "show_name": True, "show_state": False, "fit_mode": "cover", "aspect_ratio": "16:9",
              "tap_action": {"action": "more-info"}, "grid_options": {"columns": span},
              "visibility": [{"condition": "state", "entity": eid, "state_not": "unavailable"}],
              "card_mod": {"style": ONLINE_DOT}})
    return c


# Reolink "Snapshots …" cameras only supply still images to another card (camera_image); never list them.
bd.ENTS = [e for e in bd.ENTS if not (e["domain"] == "camera" and "_snapshots_" in e["entity_id"])]
bd.CAMERAS = [e for e in bd.CAMERAS if "_snapshots_" not in e["entity_id"]]
bd.CAMERA_FLAGS = {}          # the mixed-up names are resolved
bd.camera_card = camera_card  # room pages use the same card as Home


def cameras_view():
    by_room = {}
    for e in bd.CAMERAS:
        by_room.setdefault(bd.room_of(e), []).append(e["entity_id"])
    sections = [{"type": "grid", "column_span": 3, "cards": [
        bd.heading("Cameras", "mdi:cctv"),
        bd.md("Every online camera, grouped by area. A camera that goes offline is hidden until it's back; "
              "the green dot means Home Assistant can reach it. Tap a camera for the live view.")]}]
    for r in sorted(by_room, key=lambda x: (x == "Unassigned", x)):
        sections.append({"type": "grid", "cards": [bd.heading(r if r != "Unassigned" else "No area yet", "mdi:map-marker", style="subtitle"),
                                                   *[camera_card(i) for i in by_room[r]]]})
    return {"title": "Cameras", "path": "cameras", "icon": "mdi:cctv", "type": "sections", "max_columns": 3,
            "theme": bd.THEME, "sections": sections}


def review_view():
    rows = [(bd.SONY_PRIMARY, "Same TV also appears as media_player.sony_tv, sony_tv_2, sony_tv_3 and remote.sony_tv (hidden on the dashboard)"),
            ("switch.great_room_smart_bridge_unassigned_smart_away", "Purpose unclear (Lutron bridge setting)")]
    flagged = "\n".join(f"| `{e}` | {bd.ROWS[e]['display_name']} | {why} |" for e, why in rows if e in bd.ROWS)
    unassigned = "\n".join(f"| `{i}` | {bd.ROWS[i]['display_name']} |" for i in bd.UNASSIGNED_CONTROLS) or "| — | None |"
    return {"title": "Review", "path": "review", "subview": True, "back_path": f"/{bd.DASH}/home", "type": "sections",
            "max_columns": 2, "theme": bd.THEME, "sections": [{"type": "grid", "column_span": 2, "cards": [
                bd.heading("Needs review", "mdi:clipboard-check-outline"),
                bd.md(f"Snapshot from the inventory on {bd.SNAPSHOT}. Nothing here changes Home Assistant.\n\n"
                      "Camera names and areas were resolved on 2026-09-26.\n\n"
                      "### Still to check\n| Entity | Name in Home Assistant | Why |\n|---|---|---|\n" + flagged),
                bd.md("### Controls with no area\n| Entity | Name in Home Assistant |\n|---|---|\n" + unassigned)]}]}


if __name__ == "__main__":
    rooms = [a for a in bd.AREAS if bd.room_entities(a)] + ["Unassigned"]
    views = {"cameras": cameras_view(), "review": review_view()}
    for r in rooms:
        v = bd.room_subview(r)
        views[v["path"]] = v
    OUT.write_text(json.dumps(views, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{len(views)} views -> {OUT.relative_to(HERE)}: {', '.join(views)}")
