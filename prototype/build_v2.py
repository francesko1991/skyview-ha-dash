"""Builds home-panel.html from template_v2.html: inlines the two sky backgrounds as data URIs.

The prototype mirrors the live dashboard by hand (demo values only); update template_v2.html when the
dashboard changes, then run this and republish the artifact.
"""
import base64
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BG = ROOT.parent / "ha-dashboard" / "backgrounds"


def uri(name):
    return "data:image/svg+xml;base64," + base64.b64encode((BG / name).read_bytes()).decode()


html = (ROOT / "template_v2.html").read_text(encoding="utf-8")
html = html.replace("__TWILIGHT__", uri("twilight.svg")).replace("__MORNING__", uri("morning.svg"))
(ROOT / "home-panel.html").write_text(html, encoding="utf-8")
print(f"home-panel.html: {len(html) // 1024} KB")
