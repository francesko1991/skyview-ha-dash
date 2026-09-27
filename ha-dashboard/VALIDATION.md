# Validation report

Checked against inventory snapshot **2026-09-27T19:34:06Z** (Home Assistant 2026.9.3, frontend 20260826.7, Mushroom 5.2.3). File checked: `live/home-panel.json`. Run `python validate_dashboard.py` to repeat.

**Result: PASS** â€” 0 error(s), 0 warning(s).

## Summary

- Syntax: home-panel.json and the theme parse with the PyYAML safe loader; no duplicate keys.
- Theme dark: background /local/backgrounds/twilight.svg (file present in backgrounds/)
- Theme light: background /local/backgrounds/morning.svg (file present in backgrounds/)
- Theme: light and dark modes each define 54 variables; all --hp-* references resolve.
- Views: 20 (3 in the tab bar, 17 subviews). Dashboard file: 7810 lines.
- Cards: tile 141, heading 81, custom:mushroom-template-card 48, vertical-stack 36, custom:mushroom-chips-card 30, picture-entity 25, grid 13, markdown 12, button 12, picture 4, horizontal-stack 3, thermostat 2, clock 1
- Tile features: light-brightness 15, media-player-playback 8, media-player-volume-slider 7, target-temperature 2, climate-hvac-modes 2, climate-preset-modes 2, vacuum-commands 1, vacuum-fan-speed 1, media-player-source 1
- Jinja templates parsed: 276
- Entities referenced: 191 â€” all exist and are enabled: yes; sensitive entities referenced: 0
- Of those, currently not reporting a normal state: 37 (21 unknown (stateless button/siren), 7 unavailable, 9 unknown). They are shown as-is, not hidden.
- Navigation links checked: 26

## Layout at phone and tablet widths

Column count computed with the sections-view rule from frontend 20260826.7: `floor((width âˆ’ 2Â·gap + gap) / (320 + gap))`, gap 8 px at â‰¤600 px wide and 32 px otherwise, capped by `max_columns`.

| View | Path | max_columns | Sections | Phone 390 px | Tablet portrait 820 px (sidebar hidden) | Tablet landscape 1180 px (sidebar collapsed, ~56 px) |
|---|---|---|---|---|---|---|
| Home | home | 3 | 5 | 1 | 2 | 3 |
| Rooms | rooms | 2 | 1 | 1 | 2 | 2 |
| Cameras | cameras | 3 | 8 | 1 | 2 | 3 |
| Review | review | 2 | 1 | 1 | 2 | 2 |
| Living Room | room-living-room | 2 | 6 | 1 | 2 | 2 |
| Kitchen | room-kitchen | 2 | 1 | 1 | 1 | 1 |
| Bedroom | room-bedroom | 2 | 1 | 1 | 1 | 1 |
| Great Room | room-great-room | 2 | 6 | 1 | 2 | 2 |
| Basement | room-basement | 2 | 3 | 1 | 2 | 2 |
| Driveway | room-driveway | 2 | 2 | 1 | 2 | 2 |
| Back Deck | room-back-deck | 2 | 3 | 1 | 2 | 2 |
| Front Gate | room-front-gate | 2 | 2 | 1 | 2 | 2 |
| Front Yard | room-front-yard | 2 | 2 | 1 | 2 | 2 |
| Garage | room-garage | 2 | 1 | 1 | 1 | 1 |
| Office | room-office | 2 | 1 | 1 | 1 | 1 |
| No area yet | room-unassigned | 2 | 4 | 1 | 2 | 2 |
| Climate | climate | 2 | 1 | 1 | 2 | 2 |
| Lights & plugs | lights | 2 | 1 | 1 | 2 | 2 |
| Media | media | 2 | 1 | 1 | 2 | 2 |
| Cleaning | cleaning | 2 | 1 | 1 | 2 | 2 |

## Entities not reporting a normal state right now

| Entity | State in snapshot |
|---|---|
| `button.basement_basement_cam_guard_go_to` | unknown (normal for stateless buttons/sirens until used) |
| `button.basement_basement_cam_ptz_down` | unknown (normal for stateless buttons/sirens until used) |
| `button.basement_basement_cam_ptz_left` | unknown (normal for stateless buttons/sirens until used) |
| `button.basement_basement_cam_ptz_right` | unknown (normal for stateless buttons/sirens until used) |
| `button.basement_basement_cam_ptz_stop` | unknown (normal for stateless buttons/sirens until used) |
| `button.basement_basement_cam_ptz_up` | unknown (normal for stateless buttons/sirens until used) |
| `button.great_room_great_room_cam_guard_go_to` | unknown (normal for stateless buttons/sirens until used) |
| `button.great_room_great_room_cam_ptz_down` | unknown (normal for stateless buttons/sirens until used) |
| `button.great_room_great_room_cam_ptz_left` | unknown (normal for stateless buttons/sirens until used) |
| `button.great_room_great_room_cam_ptz_right` | unknown (normal for stateless buttons/sirens until used) |
| `button.great_room_great_room_cam_ptz_up` | unknown (normal for stateless buttons/sirens until used) |
| `button.living_room_living_room_cam_guard_go_to` | unknown (normal for stateless buttons/sirens until used) |
| `button.living_room_living_room_cam_ptz_down` | unknown (normal for stateless buttons/sirens until used) |
| `button.living_room_living_room_cam_ptz_left` | unknown (normal for stateless buttons/sirens until used) |
| `button.living_room_living_room_cam_ptz_right` | unknown (normal for stateless buttons/sirens until used) |
| `button.living_room_living_room_cam_ptz_stop` | unknown (normal for stateless buttons/sirens until used) |
| `button.living_room_living_room_cam_ptz_up` | unknown (normal for stateless buttons/sirens until used) |
| `button.living_room_vacuum_full_cleaning` | unknown (normal for stateless buttons/sirens until used) |
| `light.basement_entry` | unavailable |
| `light.bedroom_hallway_light` | unavailable |
| `light.guest_room_hallway_light` | unavailable |
| `light.living_room_lamp` | unavailable |
| `sensor.front_door_last_access` | unknown |
| `sensor.polestar_2_average_energy_consumption` | unknown |
| `sensor.polestar_2_average_speed` | unknown |
| `sensor.polestar_2_charging_target_level` | unknown |
| `sensor.polestar_2_charging_target_setting_type` | unknown |
| `sensor.polestar_2_estimated_charging_time_to_target_distance` | unknown |
| `sensor.polestar_2_estimated_fully_charged_time` | unknown |
| `sensor.polestar_2_trip_meter_automatic` | unknown |
| `sensor.polestar_2_trip_meter_manual` | unknown |
| `siren.basement_basement_cam_siren` | unknown (normal for stateless buttons/sirens until used) |
| `siren.great_room_great_room_cam_siren` | unknown (normal for stateless buttons/sirens until used) |
| `siren.living_room_living_room_cam_siren` | unknown (normal for stateless buttons/sirens until used) |
| `switch.night_light_socket` | unavailable |
| `switch.recliners_socket` | unavailable |
| `switch.record_player_socket` | unavailable |

## Errors

None.

## Warnings

None.

## Not checked locally

- Actual rendering in the Home Assistant app (fonts, exact tile heights, how the Mushroom chips wrap).
- Template results against live states (syntax is checked; values are rendered by Home Assistant).
- Camera live view and snapshots (no images or streams were requested).
- Service calls (nothing was called).
