"""Write docs/lid_layout.svg: top view of the lid (holes) and what goes underneath, in cm."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from boxtracker.config import DEFAULTS

S = 22          # px per cm
W, H = 30, 24   # drawing area in cm (lid 26 x 21 shown)
ox, oy = W / 2, H / 2

def X(x): return (ox + x) * S
def Y(y): return (oy - y) * S   # +y (far) is up in the drawing

pts = DEFAULTS["lid"]["points"]
col = {"R": "#2f7cff", "L": "#22b04a", "A": "#e53935"}
lab = {"R": "R  blue · LEFT hand", "L": "L  green · RIGHT hand", "A": "A  red · assistant", "U": "U  black dot (no hole)"}
o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W*S} {H*S+60}" font-family="system-ui,sans-serif" font-size="13">',
     f'<rect width="100%" height="100%" fill="#fff"/>']
# cm grid
for i in range(-15, 16):
    o.append(f'<line x1="{X(i)}" y1="{Y(-12)}" x2="{X(i)}" y2="{Y(12)}" stroke="#eee"/>')
for j in range(-12, 13):
    o.append(f'<line x1="{X(-15)}" y1="{Y(j)}" x2="{X(15)}" y2="{Y(j)}" stroke="#eee"/>')
# lid outline (minimum size)
o.append(f'<rect x="{X(-13)}" y="{Y(10.5)}" width="{26*S}" height="{21*S}" fill="none" stroke="#999" stroke-dasharray="6 4"/>')
o.append(f'<text x="{X(-12.8)}" y="{Y(10.5)+16}" fill="#999">lid, at least 26 x 21 cm</text>')
# anatomy underneath (dashed, from the sim's liver/gallbladder meshes)
o.append(f'<ellipse cx="{X(-3)}" cy="{Y(3)}" rx="{8*S}" ry="{7.5*S}" fill="#b5651d22" stroke="#b5651d" stroke-dasharray="4 3"/>')
o.append(f'<text x="{X(-9.5)}" y="{Y(-3.6)}" fill="#b5651d">liver sponge, top 10 cm below lid</text>')
o.append(f'<ellipse cx="{X(0)}" cy="{Y(4.5)}" rx="{2*S}" ry="{4.5*S}" fill="#4caf5033" stroke="#2e7d32" stroke-dasharray="4 3"/>')
o.append(f'<text x="{X(2.3)}" y="{Y(8)}" fill="#2e7d32">gallbladder bag, top 9.5 cm below</text>')
o.append(f'<line x1="{X(0)}" y1="{Y(0.3)}" x2="{X(-2.8)}" y2="{Y(-2.5)}" stroke="#c9a400" stroke-width="5" stroke-linecap="round"/>')
o.append(f'<line x1="{X(-6)}" y1="{Y(3.5)}" x2="{X(-3)}" y2="{Y(-8)}" stroke="#c9a400" stroke-width="5" stroke-linecap="round"/>')
o.append(f'<text x="{X(-12.5)}" y="{Y(-6.2)}" fill="#8a7300">straws (ducts), 12 cm below</text>')
# centre cross
o.append(f'<line x1="{X(-13)}" y1="{Y(0)}" x2="{X(13)}" y2="{Y(0)}" stroke="#555"/><line x1="{X(0)}" y1="{Y(10.5)}" x2="{X(0)}" y2="{Y(-10.5)}" stroke="#555"/>')
o.append(f'<text x="{X(0.2)}" y="{Y(0)-4}" fill="#555">centre</text>')
for k, (x, y) in pts.items():
    xc, yc = x * 100, y * 100
    if k == "U":
        o.append(f'<circle cx="{X(xc)}" cy="{Y(yc)}" r="{0.6*S}" fill="#111"/>')
    else:
        o.append(f'<circle cx="{X(xc)}" cy="{Y(yc)}" r="{0.5*S}" fill="#fff" stroke="{col[k]}" stroke-width="4"/>')
    side = "left" if xc < 0 else "right"
    fb = "far" if yc > 0 else "near"
    tx = X(xc) + (12 if xc < 8 else -150)
    o.append(f'<text x="{tx}" y="{Y(yc)+28}" font-weight="600" fill="{col.get(k, "#111")}">{lab[k]}</text>')
    o.append(f'<text x="{tx}" y="{Y(yc)+44}" fill="#444">{abs(xc):g} {side}, {abs(yc):g} {fb}</text>')
o.append(f'<text x="{X(-6)}" y="{H*S+40}" font-size="16" font-weight="600">▲ YOU stand here (near edge) ▲</text>')
o.append('</svg>')
p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "lid_layout.svg")
open(p, "w").write("\n".join(o))
print(p)
