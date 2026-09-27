"""Draws the Home Panel sky backgrounds as small SVG files (no outside services).

  twilight.svg  dark mode: navy sky, faint stars, champagne/rose horizon glow, hill line
  morning.svg   light mode: pale sky warming to the horizon, soft hill line

Colours follow the Twilight sky palette. The composition keeps the centre calm so cards
stay readable; interest sits at the top (stars) and bottom (glow, hills) so it works for
portrait phones and landscape tablets alike (the image is scaled to cover the screen).
Re-run to regenerate; star positions are seeded, so output is stable.
"""
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
W, H = 1600, 1000  # drawn landscape; "slice" scaling crops the sides on phones

# Two overlapping hill lines (gentle, like a distant ridge). x from -50 to W+50.
RIDGE_FAR = [(-50, 905), (180, 880), (420, 892), (640, 868), (880, 884), (1120, 860), (1360, 878), (1650, 866)]
RIDGE_NEAR = [(-50, 950), (260, 928), (540, 944), (820, 922), (1100, 940), (1380, 926), (1650, 938)]


def ridge_path(points):
    """Smooth closed path through the points down to the bottom edge."""
    d = [f"M{points[0][0]},{H}", f"L{points[0][0]},{points[0][1]}"]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        mx = (x0 + x1) / 2
        d.append(f"C{mx:.0f},{y0} {mx:.0f},{y1} {x1},{y1}")
    d.append(f"L{points[-1][0]},{H}Z")
    return " ".join(d)


def stars(seed=7, count=90):
    rnd = random.Random(seed)
    out = []
    for _ in range(count):
        x, y = rnd.uniform(0, W), rnd.uniform(0, H * 0.55) ** 1.0
        y = (y / (H * 0.55)) ** 1.6 * H * 0.55          # denser near the top
        r = rnd.choice([0.7, 0.8, 0.9, 1.0, 1.2, 1.5])
        op = rnd.uniform(0.12, 0.5) * (1 - y / (H * 0.6))  # fade toward the horizon
        out.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{r}" fill-opacity="{op:.2f}"/>')
    return "".join(out)


def twilight():
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid slice">
<defs>
<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#060A16"/><stop offset=".45" stop-color="#0B1222"/>
<stop offset=".78" stop-color="#18223F"/><stop offset="1" stop-color="#2A2A48"/></linearGradient>
<radialGradient id="glow" cx=".5" cy="1" r=".75" fx=".5" fy="1">
<stop offset="0" stop-color="#D9BE86" stop-opacity=".30"/><stop offset=".35" stop-color="#E99A92" stop-opacity=".12"/>
<stop offset="1" stop-color="#E99A92" stop-opacity="0"/></radialGradient>
</defs>
<rect width="{W}" height="{H}" fill="url(#sky)"/>
<rect width="{W}" height="{H}" fill="url(#glow)"/>
<g fill="#EAF0FF">{stars()}</g>
<path d="{ridge_path(RIDGE_FAR)}" fill="#0E1528"/>
<path d="{ridge_path(RIDGE_NEAR)}" fill="#080D19"/>
</svg>
'''


def morning():
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid slice">
<defs>
<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#D6E2F2"/><stop offset=".5" stop-color="#E9EFF7"/>
<stop offset=".85" stop-color="#F3EFEA"/><stop offset="1" stop-color="#F6EADB"/></linearGradient>
<radialGradient id="glow" cx=".5" cy="1" r=".7" fx=".5" fy="1">
<stop offset="0" stop-color="#F4D9A8" stop-opacity=".55"/><stop offset="1" stop-color="#F4D9A8" stop-opacity="0"/></radialGradient>
</defs>
<rect width="{W}" height="{H}" fill="url(#sky)"/>
<rect width="{W}" height="{H}" fill="url(#glow)"/>
<path d="{ridge_path(RIDGE_FAR)}" fill="#DCE3EC"/>
<path d="{ridge_path(RIDGE_NEAR)}" fill="#CFD8E4"/>
</svg>
'''


if __name__ == "__main__":
    for name, svg in (("twilight.svg", twilight()), ("morning.svg", morning())):
        (HERE / name).write_text(svg, encoding="utf-8")
        print(f"{name}: {len(svg.encode()) / 1024:.1f} KB")
