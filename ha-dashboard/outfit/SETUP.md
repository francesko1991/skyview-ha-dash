# What to wear: setup

Adds an **Outfit chip** (tap to refresh, hold for details) and a **What to wear note** to the Home Panel header. The advice comes from OpenAI through Home Assistant's AI Task, using today's forecast. On work days before departure, it also uses the drive time to work.

**Sent to OpenAI:** the local time, weather numbers, your departure time and the drive minutes and miles. **Never sent:** names, addresses or locations.

## Files

| File | Where it goes |
|---|---|
| `script.yaml` | Replaces the script **Refresh Weather Clothing Advice** (Scripts → ⋮ → Edit in YAML) |
| `automation.yaml` | New automation (Automations → Create → ⋮ → Edit in YAML) |
| `header_section.yaml` | Home Panel header section (pencil → ⋮ on the clock section → Edit section) |

## Steps

1. **Create four helpers** in **Settings → Devices & services → Helpers → Create helper**:
   - **Text:** name `Outfit headline`, max length 40. This creates `input_text.outfit_headline`.
   - **Text:** name `Outfit icon`, max length 40. This creates `input_text.outfit_icon`.
   - **Date and/or time:** name `Outfit updated`, type *Date and time*. This creates `input_datetime.outfit_updated`.
   - **Date and/or time:** name `Work departure`, type *Time*. This creates `input_datetime.work_departure`. Open it and set it to **7:00 AM**.

   The existing `input_text.weather_clothing_advice` is reused for the advice text.
2. **Optional: commute.** Go to **Add integration → Waze Travel Time**, and name it `Work commute`. Set the origin to your home zone and the destination to your work address, entered only in Home Assistant. Choose the US region. Check that the entity is `sensor.work_commute`. Without it, the advice is weather-only.
3. **Replace the script** with `script.yaml`, then save. Press **Run** once. This makes one OpenAI request.
4. **Create the automation** from `automation.yaml`. It refreshes 45 minutes before departure on weekdays (6:15 AM), at 7:30 AM on weekends, and when the weather condition changes (at most hourly, between 5:30 AM and 10 PM).
5. **Replace the header section** with `header_section.yaml`.

Work days are assumed to be Monday to Friday. For holidays, the Workday integration can be added later.
