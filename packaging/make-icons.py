"""Generate the OpenCorsair app icons (full, medium, small) as SVG.

Recreated as vectors from the designer sheet; sizes are in a 1080 grid.
Usage: python3 packaging/make-icons.py [outdir]
"""
import math, sys
out = sys.argv[1] if len(sys.argv) > 1 else "assets/icons"
C = 540

def blade_path(L, h):
    R = ((L / 2) ** 2 + h * h) / (2 * h)
    return f"M0 0A{R:.1f} {R:.1f} 0 0 1 {L} 0A{R:.1f} {R:.1f} 0 0 1 0 0Z"

def arc(a0, a1, R, color):
    p = lambda a: (C + R * math.cos(math.radians(a)), C + R * math.sin(math.radians(a)))
    (x0, y0), (x1, y1) = p(a0), p(a1)
    large = 1 if (a1 - a0) % 360 > 180 else 0
    return f'<path d="M{x0:.1f} {y0:.1f}A{R} {R} 0 {large} 1 {x1:.1f} {y1:.1f}" stroke="{color}"/>'

def blades(L, h, angles=(55, 175, 295)):
    d = blade_path(L, h)
    return (f'<g fill="#f2f3f7" transform="translate({C} {C})">'
            + "".join(f'<path d="{d}" transform="rotate({a})"/>' for a in angles) + "</g>")

def svg(body):
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1080">\n'
            '  <rect width="1080" height="1080" rx="240" fill="#11161c"/>\n  ' + "\n  ".join(body) + "\n</svg>\n")

BLUE, ORANGE, GREEN = "#3e8bff", "#ff8a3d", "#3fc4a5"
full = svg([
    '<g fill="none" stroke-width="63" stroke-linecap="round">'
    + arc(-60, 14, 376.5, BLUE) + arc(30, 104, 376.5, ORANGE) + arc(120, 194, 376.5, GREEN) + "</g>",
    blades(330, 58),
    f'<circle cx="{C}" cy="{C}" r="62" fill="#11161c"/>',
    f'<circle cx="{C}" cy="{C}" r="29" fill="{BLUE}"/>',
])
medium = svg([
    f'<g fill="none" stroke-width="105" stroke-linecap="round">{arc(-62, 242, 350, BLUE)}</g>',
    blades(330, 70),
])
small = svg([blades(430, 92)])
open(f"{out}/opencorsair.svg", "w").write(full)
open(f"{out}/opencorsair-medium.svg", "w").write(medium)
open(f"{out}/opencorsair-small.svg", "w").write(small)
