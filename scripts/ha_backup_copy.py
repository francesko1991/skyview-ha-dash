"""On-demand copy of Home Assistant backups to this PC (ha-backups/), keeping the 2 newest.

Dry run by default: lists what would be downloaded and which older copies would go to the Recycle Bin.
With --apply: downloads the newest backups that aren't here yet (still encrypted, exactly as stored), checks each
copy's size and that the archive opens, then sends copies beyond the newest KEEP to the Windows Recycle Bin
(recoverable; nothing is permanently deleted). Reads from Home Assistant only (backup/info + backup download).
Prints dates, sizes and pass/fail only — never backup contents or the encryption key.
"""
import json
import subprocess
import sys
import tarfile
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ha_inventory as hi  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "ha-backups"
KEEP = 2
AGENT = "hassio.local"


def local_name(b):
    d = datetime.fromisoformat(b["date"]).strftime("%Y%m%d-%H%M")
    return f"ha-backup-{d}-{b['backup_id']}.tar"


def local_copies():
    return sorted(DEST.glob("ha-backup-*.tar"), key=lambda p: p.name, reverse=True)  # names sort by date


def verify(path, expected):
    size = path.stat().st_size
    if expected and size != expected:
        return f"size {size} ≠ expected {expected}"
    try:
        with tarfile.open(path) as t:
            names = t.getnames()
        if "./backup.json" not in names and "backup.json" not in names:
            return "archive opens but has no backup metadata"
    except tarfile.TarError as e:
        return f"archive does not open ({type(e).__name__})"
    return None


def to_recycle_bin(path):
    ps = ("Add-Type -AssemblyName Microsoft.VisualBasic; "
          "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($args[0], 'OnlyErrorDialogs', 'SendToRecycleBin')")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps, str(path)], check=True)


def main():
    apply = "--apply" in sys.argv
    ws = hi.HAWebSocket()
    if not ws.call("auth/current_user").get("is_admin"):
        sys.exit("Needs an admin token (backups are admin-only).")
    ws.ws.send(json.dumps({"id": 99, "type": "backup/info"}))
    while True:
        m = json.loads(ws.ws.recv())
        if m.get("id") == 99:
            break
    ws.close()
    if not m.get("success"):
        sys.exit("Could not list backups.")
    bks = sorted([b for b in m["result"]["backups"] if AGENT in (b.get("agents") or {})], key=lambda b: b["date"], reverse=True)
    wanted = bks[:KEEP]
    have = {p.name for p in local_copies()}
    todo = [b for b in wanted if local_name(b) not in have]
    print(f"{'APPLYING' if apply else 'DRY RUN (nothing downloaded or moved)'}: keep the {KEEP} newest backups in {DEST.relative_to(ROOT)}")
    for b in wanted:
        size = (b["agents"][AGENT] or {}).get("size") or 0
        state = "already here" if local_name(b) in have else "download"
        print(f"  {b['date'][:16].replace('T', ' ')}  {size / 1e6:.0f} MB  encrypted={b['agents'][AGENT].get('protected')}  -> {state}")
    keep_names = {local_name(b) for b in wanted}
    extra = [p for p in local_copies() if p.name not in keep_names]
    for p in extra:
        print(f"  older copy {p.name[:24]}… -> Recycle Bin")
    if not apply:
        return
    DEST.mkdir(exist_ok=True)
    for b in todo:
        size = (b["agents"][AGENT] or {}).get("size")
        part = DEST / (local_name(b) + ".part")
        with requests.get(f"{hi.URL}/api/backup/download/{b['backup_id']}", params={"agent_id": AGENT},
                          headers={"Authorization": f"Bearer {hi.TOKEN}"}, stream=True, timeout=600) as r:
            if r.status_code != 200:
                sys.exit(f"Download failed: HTTP {r.status_code}")
            with open(part, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        problem = verify(part, size)
        if problem:
            part.unlink()  # our own incomplete download
            sys.exit(f"Copy of {b['date'][:16]} failed verification: {problem}. Nothing else changed.")
        final = DEST / local_name(b)
        part.rename(final)
        print(f"  saved {final.name[:24]}…  {final.stat().st_size / 1e6:.0f} MB  verified (size matches, archive opens)")
    for p in [p for p in local_copies() if p.name not in keep_names]:
        to_recycle_bin(p)
        print(f"  moved {p.name[:24]}… to the Recycle Bin")
    print(f"Done: {len(local_copies())} copies in {DEST.relative_to(ROOT)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        sys.exit(f"ERROR: {type(e).__name__}: {hi.scrub(e)}")
