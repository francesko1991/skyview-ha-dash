# Optional SSH checks (you run these)

Only for what the API can't see. Open **Settings → Add-ons → Advanced SSH & Web Terminal → Open Web UI**. Every command
below only reads. Don't paste any output containing passwords, tokens or addresses into chat; the one-line summaries below
are enough.

| Check | Command | Healthy looks like |
|---|---|---|
| Database size | `du -h /config/home-assistant_v2.db` | Under ~2 GB for a home this size; growing steadily = review recorder settings |
| Database integrity | `sqlite3 /config/home-assistant_v2.db "PRAGMA quick_check;"` | `ok` (run at a quiet time; takes a while on large files) |
| Largest files in config | `du -ah /config \| sort -rh \| head -15` | Database, backups and media on top; nothing surprising |
| Custom components on disk | `ls /config/custom_components` | Every folder is one you know (and ideally managed by HACS) |
| `secrets.yaml` present | `test -f /config/secrets.yaml && echo present` | `present` if your YAML uses `!secret` |
| Config check | `ha core check` | No errors (same as Developer tools → YAML → Check configuration) |
| Backup files | `ls -lh /backup` | Matches the backup list in the report |

A restore test is best done on a spare machine or a virtual machine from a downloaded backup, never on the live system.
