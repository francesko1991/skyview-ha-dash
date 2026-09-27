# Home Panel dashboard: install and revise

A Home Assistant dashboard based on the approved Home Panel prototype. **Status: installed on 2026-09-26** as the `home-panel` dashboard, with the theme in `/config/themes/home_panel.yaml`. The live dashboard in Home Assistant is now the master copy (see [Revising](#revising)).

## Files

| File | What it is |
|---|---|
| `live/home-panel.json` | **Master copy.** The latest read-only pull of the live dashboard. |
| `home_panel_dashboard.yaml` | The original generated draft that was pasted in. Kept for reference; it isn't updated anymore. |
| `themes/home_panel.yaml` | Project copy of the theme. Keep it in sync with `/config/themes/home_panel.yaml`. |
| `build_dashboard.py` | **Retired.** It created the first draft only. Don't run it, because it overwrites the draft files and ignores live edits. |
| `validate_dashboard.py` | Local checks: syntax, entities, card compatibility, layout. It checks the master copy and writes `VALIDATION.md`. |
| `VALIDATION.md` | Latest validation results. |

## What it needs

- **Home Assistant 2026.9** or newer. It uses a sections layout, tile features, heading badges and the clock card.
- **Mushroom 5.x.** It's already installed through HACS and registered as a dashboard resource. The dashboard uses it only for the row of live alert chips at the top of Home (`custom:mushroom-chips-card`). If Mushroom is ever removed, delete that one card.
- **The theme is optional.** Without it, the dashboard uses Home Assistant's default colours.

No other custom cards are used. Bubble Card, button-card, card-mod, mini-media-player and the rest stay installed but aren't needed by this dashboard.

## Install (sign in with an admin account)

1. Go to **Settings → Dashboards → Add dashboard → New dashboard from scratch**. Name it `Home Panel` and set the URL to **`home-panel`**.
   - Links between views assume this URL. If you pick another one, replace every `/home-panel/` in the YAML with your own path.
2. Open the new dashboard and click the pencil (**Edit**). In the three-dot menu, open **Raw configuration editor**.
3. Replace everything in the editor with the contents of `home_panel_dashboard.yaml`, then click **Save**.
4. Check it on your phone and tablet. To remove it, delete the dashboard under Settings → Dashboards.

### Optional: the theme

1. Copy `themes/home_panel.yaml` into a `themes` folder inside your Home Assistant config folder.
2. Make sure `configuration.yaml` loads themes from that folder. This changes your configuration, so review it first:
   ```yaml
   frontend:
     themes: !include_dir_merge_named themes
   ```
3. Restart Home Assistant, or run the `frontend.reload_themes` action. Every view already asks for the `Home Panel` theme, so it applies as soon as it's loaded. It has light and dark versions that follow your device.

## Behaviour to know about

- **Lock:** the lock tile shows Home Assistant's real state, which is currently *Unknown*. While it's unknown or unavailable, a warning explains that the state isn't confirmed. **Lock…** and **Unlock…** always ask for confirmation before sending a command.
- **Sirens** ask before sounding, and so does **Turn all lights off…**.
- **Cameras** show live snapshots (`camera_view: auto`). Tap one to open the live view. Cameras whose names don't match their entity ID or area are marked **"· check name"** and are shown in the area Home Assistant has. The Cameras view explains each one. Nothing is renamed or moved.
- **Unavailable lights and plugs** move by themselves into an "Unavailable right now" group, and move back when they return.
- **Display names** are tidied only on the cards, for example "laundry light 3" becomes "Laundry Light 3" and "Fluent" is dropped from camera names. Entity names in Home Assistant don't change.
- **Review** (open it from the alert chips) lists the flagged names and the controls with no area, as of the inventory snapshot.

## Revising

The live dashboard is the master copy. Make changes in Home Assistant, then pull them into the project.

1. **Edit live.** Open Home Panel, click the pencil, and change cards in the visual editor or the raw configuration editor. Save.
2. **Pull it (read-only):**
   ```
   scripts/run-ha.ps1 --pull home-panel
   ```
   This saves `live/home-panel.json`. It refuses to save if the dashboard contains a URL, IP address or token. Pull after each batch of edits; the previous pull is your backup until you pull again.
3. **Check it:** run `python validate_dashboard.py` (needs `pip install pyyaml jinja2`). If you renamed or moved devices, refresh the inventory first with `scripts/run-ha.ps1 --inventory`.
4. **Update the prototype** (`../prototype/`) to match, and republish it.

- **Colours and shapes:** edit the `hp-*` tokens in `/config/themes/home_panel.yaml`, then run the `frontend.reload_themes` action (Settings → Tools → Actions). Copy the same change into `themes/home_panel.yaml` here. The dashboard itself doesn't need to change.
- **To restore** a bad edit, paste the last good `live/home-panel.json` into the raw configuration editor. Home Assistant accepts JSON there, since JSON is valid YAML.
- If you fix the mixed-up cameras in Home Assistant, remove their "· check name" text on the live dashboard and update the Review view, then pull. The prototype's flags get updated to match.
