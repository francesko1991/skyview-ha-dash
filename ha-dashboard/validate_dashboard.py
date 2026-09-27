"""Local validation of the Home Panel dashboard draft. Never contacts Home Assistant.

Checks: YAML syntax (duplicate keys too), Jinja template syntax, every referenced entity
against the latest read-only inventory, card/feature/option compatibility with the
Home Assistant frontend and Mushroom versions found on the server, navigation targets,
theme completeness, and the sections-view column layout at phone and tablet widths.

Needs PyYAML and Jinja2 (only for this check):  python validate_dashboard.py
Writes VALIDATION.md next to this file and exits non-zero on any error.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

import jinja2
import yaml

HERE = Path(__file__).resolve().parent
INV = json.loads((HERE.parent / "inventory" / "ha_inventory.json").read_text(encoding="utf-8"))
INSPECT = json.loads((HERE.parent / "inventory" / "dashboard_inspection.json").read_text(encoding="utf-8"))
ROWS = {e["entity_id"]: e for e in INV["entities"]}

# --- Reference data, taken from home-assistant/frontend tag 20260826.7 (pinned by HA 2026.9.3)
# src/panels/lovelace/create-element/create-card-element.ts (always-loaded + lazy types)
HA_CARDS = set("""alarm-panel alert button clock conditional entities entity entity-button entity-filter gauge glance grid
heading history-graph horizontal-stack humidifier iframe light logbook map markdown media-control picture picture-elements
picture-entity picture-glance plant-status sensor shopping-list statistics-graph thermostat tile todo-list toggle-group
vertical-stack weather-forecast""".split())
# src/panels/lovelace/card-features/types.ts
HA_FEATURES = set("""alarm-modes area-controls bar-gauge button climate-fan-modes climate-hvac-modes climate-preset-modes
climate-swing-horizontal-modes climate-swing-modes counter-actions cover-open-close cover-position cover-position-favorite
cover-tilt cover-tilt-favorite cover-tilt-position date-set fan-direction fan-oscillate fan-preset-modes fan-speed
humidifier-modes humidifier-toggle lawn-mower-commands light-brightness light-color-favorites light-color-temp light-effect
lock-commands lock-open-door media-player-playback media-player-sound-mode media-player-source media-player-volume-buttons
media-player-volume-slider numeric-input precipitation-forecast select-options target-humidity target-temperature
temperature-forecast toggle trend-graph update-actions vacuum-commands vacuum-fan-speed valve-open-close valve-position
valve-position-favorite water-heater-operation-modes""".split())
VACUUM_COMMANDS = {"start_pause", "stop", "clean_spot", "locate", "return_home"}
PLAYBACK_CONTROLS = {"turn_on", "turn_off", "power", "media_play", "media_pause", "media_play_pause", "media_stop",
                     "media_previous_track", "media_next_track", "volume_down", "volume_up", "volume_mute", "shuffle_set", "repeat_set"}
CONDITIONS = {"state", "numeric_state", "screen", "user", "time", "location", "and", "or", "not"}
COMMON = {"type", "grid_options", "visibility", "view_layout", "layout_options"}
CARD_KEYS = {
    "tile": COMMON | {"entity", "name", "hide_state", "state_content", "icon", "color", "show_entity_picture", "vertical",
                      "tap_action", "hold_action", "double_tap_action", "icon_tap_action", "icon_hold_action",
                      "icon_double_tap_action", "features", "features_position", "time_format"},
    "heading": COMMON | {"heading_style", "heading", "icon", "tap_action", "badges"},
    "markdown": COMMON | {"content", "text_only", "title", "card_size", "entity_ids", "theme", "show_empty"},
    "picture-entity": COMMON | {"entity", "name", "image", "camera_image", "camera_view", "state_image", "state_filter",
                                "aspect_ratio", "fit_mode", "tap_action", "hold_action", "double_tap_action", "show_name",
                                "show_state", "theme"},
    "button": COMMON | {"entity", "name", "show_name", "icon", "icon_height", "show_icon", "theme", "tap_action",
                        "hold_action", "double_tap_action", "state_color", "show_state"},
    "custom:mushroom-chips-card": COMMON | {"chips", "alignment"},
    # Mushroom v5.2.3 src/cards/template-card/template-card-config.ts
    "custom:mushroom-template-card": COMMON | {"entity", "area", "primary", "secondary", "color", "icon", "picture",
                                               "badge_icon", "badge_text", "badge_color", "vertical", "multiline_secondary",
                                               "tap_action", "hold_action", "double_tap_action", "icon_tap_action",
                                               "icon_hold_action", "icon_double_tap_action", "features", "features_position",
                                               "entity_id"},
    "clock": COMMON | {"title", "clock_style", "clock_size", "show_seconds", "seconds_motion", "time_format", "time_zone",
                       "no_background", "date_format", "border", "ticks", "face_style"},
}
# Mushroom v5.2.3 src/utils/lovelace/chip/types.ts (TemplateChipConfig)
MUSH_TEMPLATE_CHIP = {"type", "entity", "hold_action", "tap_action", "double_tap_action", "content", "icon", "icon_color",
                      "picture", "entity_id"}
# Mushroom v5.2.3 WeatherChipConfig
MUSH_WEATHER_CHIP = {"type", "entity", "tap_action", "hold_action", "double_tap_action", "show_temperature", "show_conditions"}
MUSH_COLORS = set("""primary accent red pink purple deep-purple indigo blue light-blue cyan teal green light-green lime yellow
amber orange deep-orange brown light-grey grey dark-grey blue-grey black white disabled""".split())
ACTIONS = {"more-info", "toggle", "navigate", "url", "perform-action", "none", "assist", "fire-dom-event"}
ENTITY_RE = re.compile(r"\b(?:binary_sensor|button|camera|climate|light|lock|media_player|number|remote|select|sensor|siren|"
                       r"sun|switch|vacuum|weather|person|device_tracker|zone)\.[a-z0-9_]+\b")

errors, warnings, info = [], [], []


class DupKeyLoader(yaml.SafeLoader):
    pass


def _map(loader, node, deep=False):
    keys = [loader.construct_object(k, deep=deep) for k, _ in node.value]
    for k, n in Counter(keys).items():
        if n > 1:
            errors.append(f"YAML duplicate key `{k}` near line {node.start_mark.line + 1}")
    return loader.construct_mapping(node, deep)


DupKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _map)


def load(path):
    try:
        return yaml.load(path.read_text(encoding="utf-8"), Loader=DupKeyLoader)
    except yaml.YAMLError as e:
        errors.append(f"YAML syntax error in {path.name}: {e}")
        return None


LIVE = HERE / "live" / "home-panel.json"
DASH_FILE = LIVE if LIVE.exists() else HERE / "home_panel_dashboard.yaml"  # live pull is the master copy
dash = load(DASH_FILE)
theme = load(HERE / "themes" / "home_panel.yaml")
if dash is None or theme is None:
    print("\n".join(errors))
    sys.exit(1)
info.append(f"Syntax: {DASH_FILE.name} and the theme parse with the PyYAML safe loader; no duplicate keys.")

# ---------------------------------------------------------------- walk
jenv = jinja2.Environment()
templates, refs, card_types, feature_types = 0, Counter(), Counter(), Counter()
view_paths = {v.get("path") for v in dash["views"]}
nav_targets = []


def check_template(s, where):
    global templates
    if "{{" in s or "{%" in s:
        templates += 1
        try:
            jenv.parse(s)
        except jinja2.TemplateSyntaxError as e:
            errors.append(f"Template syntax error at {where}: {e}")


def check_action(a, where):
    if not isinstance(a, dict):
        return
    act = a.get("action")
    if act not in ACTIONS:
        errors.append(f"{where}: unknown action `{act}`")
    if act == "navigate":
        nav_targets.append((a.get("navigation_path"), where))
    if act == "perform-action":
        svc = a.get("perform_action", "")
        dom = svc.split(".")[0]
        tgt = a.get("target", {}).get("entity_id")
        for t in ([tgt] if isinstance(tgt, str) else tgt or []):
            if t.split(".")[0] != dom and dom != "homeassistant":
                errors.append(f"{where}: {svc} targets {t} (domain mismatch)")
        if svc.split(".")[0] in ("lock", "siren") and "confirmation" not in a and "lock.front_door" in str(tgt):
            errors.append(f"{where}: {svc} without confirmation")


def check_conditions(conds, where):
    for c in conds or []:
        ct = c.get("condition")
        if ct not in CONDITIONS:
            errors.append(f"{where}: unknown visibility condition `{ct}`")
        if ct in ("and", "or", "not"):
            check_conditions(c.get("conditions"), where)
        if c.get("entity"):
            refs[c["entity"]] += 1


def check_tile_features(card, where):
    e = ROWS.get(card["entity"])
    if not e:
        return
    dom, caps, feats = e["domain"], e["capabilities"], set(e["features"])
    for f in card.get("features", []):
        ft = f.get("type")
        feature_types[ft] += 1
        if ft not in HA_FEATURES:
            errors.append(f"{where}: unknown tile feature `{ft}`")
            continue
        need = {"light-brightness": "light", "target-temperature": "climate", "climate-hvac-modes": "climate",
                "climate-preset-modes": "climate", "vacuum-commands": "vacuum", "vacuum-fan-speed": "vacuum",
                "media-player-playback": "media_player", "media-player-volume-slider": "media_player",
                "media-player-source": "media_player", "lock-commands": "lock"}.get(ft)
        if need and dom != need:
            errors.append(f"{where}: feature {ft} on {dom} entity")
        if ft == "light-brightness" and not any(m != "onoff" for m in caps.get("supported_color_modes", [])):
            errors.append(f"{where}: {card['entity']} has no brightness")
        if ft == "climate-hvac-modes" and not set(f.get("hvac_modes", [])) <= set(caps.get("hvac_modes", [])):
            errors.append(f"{where}: hvac_modes not supported by {card['entity']}")
        if ft == "climate-preset-modes" and not set(f.get("preset_modes", [])) <= set(caps.get("preset_modes", [])):
            errors.append(f"{where}: preset_modes not supported by {card['entity']}")
        if ft == "target-temperature" and "target_temperature" not in feats:
            errors.append(f"{where}: {card['entity']} has no target temperature")
        if ft == "vacuum-commands":
            bad = set(f.get("commands", [])) - VACUUM_COMMANDS
            if bad:
                errors.append(f"{where}: invalid vacuum commands {bad}")
            needs = {"start_pause": "start", "return_home": "return_home", "locate": "locate", "clean_spot": "clean_spot", "stop": "stop"}
            for c in f.get("commands", []):
                if needs[c] not in feats:
                    errors.append(f"{where}: vacuum lacks {needs[c]} for {c}")
        if ft == "vacuum-fan-speed" and "fan_speed" not in feats:
            errors.append(f"{where}: vacuum has no fan speed")
        if ft == "media-player-playback":
            bad = set(f.get("controls", [])) - PLAYBACK_CONTROLS
            if bad:
                errors.append(f"{where}: invalid playback controls {bad}")
            if not ({"pause", "play"} & feats):
                errors.append(f"{where}: {card['entity']} can't play/pause")
        if ft == "media-player-volume-slider" and "volume_set" not in feats:
            errors.append(f"{where}: {card['entity']} has no volume_set")
        if ft == "media-player-source" and "select_source" not in feats:
            errors.append(f"{where}: {card['entity']} has no select_source")


def walk_card(card, where):
    t = card.get("type")
    card_types[t] += 1
    if t.startswith("custom:"):
        if t.startswith("custom:mushroom-") and not any("mushroom" in r["url"] for r in INSPECT["resources"]):
            errors.append(f"{where}: Mushroom resource not registered on the server")
        elif t not in ("custom:mushroom-chips-card", "custom:mushroom-template-card"):
            errors.append(f"{where}: unexpected custom card {t}")
    elif t not in HA_CARDS:
        errors.append(f"{where}: unknown card type `{t}`")
    allowed = CARD_KEYS.get(t)
    if "card_mod" in card:  # card-mod (HACS) styling; needs its resource registered
        if not any("card-mod" in r["url"] for r in INSPECT["resources"]):
            errors.append(f"{where}: uses card_mod but card-mod is not registered on the server")
        allowed = (allowed | {"card_mod"}) if allowed else allowed
    if allowed and set(card) - allowed:
        errors.append(f"{where}: {t} has unsupported option(s) {sorted(set(card) - allowed)}")
    go = card.get("grid_options", {})
    if go:
        cols = go.get("columns")
        if not (cols == "full" or (isinstance(cols, int) and 1 <= cols <= 12)):
            errors.append(f"{where}: grid_options.columns={cols}")
        if isinstance(cols, int) and cols % 3:
            warnings.append(f"{where}: grid_options.columns={cols} is not a multiple of 3 (sections grid prefers 3/6/9/12)")
    check_conditions(card.get("visibility"), where)
    for k in ("tap_action", "hold_action", "double_tap_action", "icon_tap_action"):
        check_action(card.get(k), f"{where}.{k}")
    if card.get("entity"):
        refs[card["entity"]] += 1
    if t == "tile":
        check_tile_features(card, where)
    if t == "heading":
        for b in card.get("badges", []):
            if b.get("type", "entity") not in ("entity", "button"):
                errors.append(f"{where}: heading badge type {b.get('type')}")
            check_action(b.get("tap_action"), where + ".badge")
    if t == "custom:mushroom-chips-card":
        for i, ch in enumerate(card["chips"]):
            w = f"{where}.chip[{i}]"
            if ch.get("type") == "conditional":  # Mushroom conditional chip: HA conditions + one inner chip
                if set(ch) - {"type", "conditions", "chip"} or not isinstance(ch.get("chip"), dict):
                    errors.append(f"{w}: conditional chip needs only conditions + chip")
                    continue
                check_conditions(ch.get("conditions"), w)
                ch, w = ch["chip"], w + ".chip"
            allowed_chip = {"template": MUSH_TEMPLATE_CHIP, "weather": MUSH_WEATHER_CHIP}.get(ch.get("type"))
            if allowed_chip is None:
                errors.append(f"{w}: chip type {ch.get('type')!r} not supported by this check")
                continue
            if set(ch) - allowed_chip:
                errors.append(f"{w}: unsupported chip option(s) {sorted(set(ch) - allowed_chip)}")
            if ch.get("type") == "weather" and ROWS.get(ch.get("entity"), {}).get("domain") != "weather":
                errors.append(f"{w}: weather chip needs a weather entity")
            ic = ch.get("icon_color", "")
            cols = [ic] if "{{" not in ic else re.findall(r"\{\{\s*'([a-z-]+)'\s+if", ic) + re.findall(r"else\s+'([a-z-]+)'\s*\}\}", ic)
            if "{{" in ic and not cols:
                errors.append(f"{w}: could not read colours from icon_color template")
            for col in cols:
                if col and col not in MUSH_COLORS:
                    errors.append(f"{w}: icon_color `{col}` is not a Mushroom colour")
            check_action(ch.get("tap_action"), w)
            for eid in ([ch["entity_id"]] if isinstance(ch.get("entity_id"), str) else ch.get("entity_id", [])):
                refs[eid] += 1
            if ch.get("entity"):
                refs[ch["entity"]] += 1
    def strings(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                yield from strings(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                yield from strings(v, f"{path}[{i}]")
        elif isinstance(node, str):
            yield path, node
    if t in ("horizontal-stack", "vertical-stack", "grid"):  # check nested cards with the same rules
        for i, sub in enumerate(card.get("cards", [])):
            walk_card(sub, f"{where}>{i}({sub.get('type')})")
        return
    for path, v in strings(card, where):
        check_template(v, path)
        if not path.endswith(".perform_action"):  # service names look like entity IDs
            refs.update(ENTITY_RE.findall(v))
    for k, v in card.items():
        if k == "target":
            tgt = v.get("entity_id")
            refs.update([tgt] if isinstance(tgt, str) else tgt or [])
        if k in ("tap_action",) and isinstance(v, dict) and isinstance(v.get("target"), dict):
            tgt = v["target"].get("entity_id")
            refs.update([tgt] if isinstance(tgt, str) else tgt or [])


layout_rows = []
WIDTHS = [("Phone 390 px", 390, 8), ("Tablet portrait 820 px (sidebar hidden)", 820, 32),
          ("Tablet landscape 1180 px (sidebar collapsed, ~56 px)", 1180 - 56, 32)]
for vi, view in enumerate(dash["views"]):
    vw = f"view `{view.get('path')}`"
    if view.get("type") != "sections":
        errors.append(f"{vw}: expected sections view")
    maxc = view.get("max_columns", 4)
    if view.get("back_path") and view["back_path"].split("/")[-1] not in view_paths:
        errors.append(f"{vw}: back_path {view['back_path']} does not exist")
    for si, sec in enumerate(view.get("sections", [])):
        span = sec.get("column_span", 1)
        if span > maxc:
            errors.append(f"{vw} section {si}: column_span {span} > max_columns {maxc}")
        for ci, card in enumerate(sec.get("cards", [])):
            walk_card(card, f"{vw}/s{si}/c{ci}({card.get('type')})")
    spans = [s.get("column_span", 1) for s in view.get("sections", [])]
    cols = []
    for label, w, gap in WIDTHS:
        c = max(1, (w - 2 * gap + gap) // (320 + gap))
        c = max(1, min(c, maxc, sum(spans) or 1))
        cols.append(c)
    layout_rows.append((view.get("title"), view.get("path"), maxc, len(spans), cols))

for path, where in nav_targets:
    m = re.fullmatch(r"/home-panel/([a-z0-9-]+)", path or "")
    if not m or m.group(1) not in view_paths:
        errors.append(f"{where}: navigation_path {path} does not match a view in this dashboard")

# ---------------------------------------------------------------- entities
missing, disabled, sensitive, bad_now = [], [], [], []
for eid in sorted(refs):
    e = ROWS.get(eid)
    if not e:
        missing.append(eid)
        continue
    if e["availability"].startswith("disabled"):
        disabled.append(eid)
    if e["sensitive"]:
        sensitive.append(eid)
    if e["availability"] in ("unavailable", "unknown"):
        note = " (normal for stateless buttons/sirens until used)" if e["domain"] in ("button", "siren") and e["availability"] == "unknown" else ""
        bad_now.append((eid, e["availability"] + note))
for eid in missing:
    errors.append(f"Entity not found in Home Assistant: {eid}")
for eid in disabled:
    errors.append(f"Entity is disabled in Home Assistant: {eid}")
for eid in sensitive:
    errors.append(f"Sensitive entity referenced: {eid}")

# ---------------------------------------------------------------- theme
tm = theme.get("Home Panel", {}).get("modes", {})
if set(tm) != {"light", "dark"}:
    errors.append("Theme must define light and dark modes")
elif set(tm["light"]) != set(tm["dark"]):
    errors.append(f"Theme modes differ: {sorted(set(tm['light']) ^ set(tm['dark']))}")
else:
    for mode, vals in tm.items():
        for k, v in vals.items():
            for ref in re.findall(r"var\(--([a-z0-9-]+)\)", str(v)):
                if ref.startswith("hp-") and ref not in vals:
                    errors.append(f"Theme {mode}: {k} references undefined --{ref}")
            v = str(v)
            if k.startswith("hp-rgb"):
                if not re.fullmatch(r"\d{1,3}, \d{1,3}, \d{1,3}", v):
                    errors.append(f"Theme {mode}: {k}={v} is not an 'r, g, b' triplet")
            elif k == "hp-radius":
                if not re.fullmatch(r"\d+px", v):
                    errors.append(f"Theme {mode}: {k}={v} is not a px size")
            elif k == "hp-background":
                img = re.search(r"url\('(/local/[^']+)'\)", v)
                if not img:
                    errors.append(f"Theme {mode}: hp-background has no /local/ image url")
                elif not (HERE / "backgrounds" / Path(img.group(1)).name).exists():
                    errors.append(f"Theme {mode}: {img.group(1)} has no matching file in backgrounds/")
                else:
                    info.append(f"Theme {mode}: background {img.group(1)} (file present in backgrounds/)")
            elif k.startswith("hp-") and k != "hp-shadow":
                if not re.fullmatch(r"#[0-9A-Fa-f]{6}|rgba\(\d{1,3}, \d{1,3}, \d{1,3}, (0|1|0?\.\d+)\)", v):
                    errors.append(f"Theme {mode}: {k}={v} is not a hex or rgba colour")
    info.append(f"Theme: light and dark modes each define {len(tm['light'])} variables; all --hp-* references resolve.")
if "Home Panel" not in INSPECT.get("themes", []):
    warnings.append("The Home Panel theme is not installed on the server yet (no themes are installed). "
                    "Without it the dashboard uses Home Assistant's default colours; everything still works.")

# ---------------------------------------------------------------- report
now_counts = Counter(a.split(" ")[0] + (" (stateless button/siren)" if "stateless" in a else "") for _, a in bad_now)
dash_yaml = DASH_FILE.read_text(encoding="utf-8")
rep = [f"# Validation report", "",
       f"Checked against inventory snapshot **{INV['meta']['generated_utc']}** (Home Assistant {INSPECT['ha_version']}, "
       f"frontend 20260826.7, Mushroom 5.2.3). File checked: `{DASH_FILE.relative_to(HERE).as_posix()}`. Run `python validate_dashboard.py` to repeat.", "",
       f"**Result: {'PASS' if not errors else 'FAIL'}** â€” {len(errors)} error(s), {len(warnings)} warning(s).", "",
       "## Summary", ""]
rep += [f"- {x}" for x in info]
rep += [f"- Views: {len(dash['views'])} ({sum(1 for v in dash['views'] if not v.get('subview'))} in the tab bar, "
        f"{sum(1 for v in dash['views'] if v.get('subview'))} subviews). Dashboard file: {dash_yaml.count(chr(10))} lines.",
        f"- Cards: " + ", ".join(f"{t} {n}" for t, n in card_types.most_common()),
        f"- Tile features: " + ", ".join(f"{t} {n}" for t, n in feature_types.most_common()),
        f"- Jinja templates parsed: {templates}",
        f"- Entities referenced: {len(refs)} â€” all exist and are enabled: {'yes' if not (missing or disabled) else 'NO'}; "
        f"sensitive entities referenced: {len(sensitive)}",
        f"- Of those, currently not reporting a normal state: {len(bad_now)} "
        f"({', '.join(f'{n} {k}' for k, n in now_counts.items())}). They are shown as-is, not hidden.",
        f"- Navigation links checked: {len(nav_targets)}", "",
        "## Layout at phone and tablet widths", "",
        "Column count computed with the sections-view rule from frontend 20260826.7: "
        "`floor((width âˆ’ 2Â·gap + gap) / (320 + gap))`, gap 8 px at â‰¤600 px wide and 32 px otherwise, capped by `max_columns`.", "",
        "| View | Path | max_columns | Sections | " + " | ".join(w[0] for w in WIDTHS) + " |",
        "|---|---|---|---|" + "---|" * len(WIDTHS)]
rep += [f"| {t} | {p} | {m} | {n} | " + " | ".join(str(c) for c in cols) + " |" for t, p, m, n, cols in layout_rows]
rep += ["", "## Entities not reporting a normal state right now", "",
        "| Entity | State in snapshot |", "|---|---|"] + [f"| `{e}` | {a} |" for e, a in bad_now]
rep += ["", "## Errors", ""] + ([f"- {e}" for e in errors] or ["None."])
rep += ["", "## Warnings", ""] + ([f"- {w}" for w in warnings] or ["None."])
rep += ["", "## Not checked locally", "",
        "- Actual rendering in the Home Assistant app (fonts, exact tile heights, how the Mushroom chips wrap).",
        "- Template results against live states (syntax is checked; values are rendered by Home Assistant).",
        "- Camera live view and snapshots (no images or streams were requested).",
        "- Service calls (nothing was called)."]
(HERE / "VALIDATION.md").write_text("\n".join(rep) + "\n", encoding="utf-8")
print("\n".join(rep[:40]))
print(f"\nErrors: {len(errors)}  Warnings: {len(warnings)}")
for e in errors[:40]:
    print("  ERROR", e)
for w in warnings[:20]:
    print("  WARN ", w)
sys.exit(1 if errors else 0)

