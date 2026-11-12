"""Regenerate H3TierExport.ico and PNG preview from vector-like primitives.
Development-only helper; runtime does not require Python or Pillow.
"""
from pathlib import Path
from PIL import Image, ImageDraw
import hashlib, json

PRODUCT = Path(__file__).resolve().parents[1]
RES = PRODUCT / "resources"

def rounded(draw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)

def render(size: int) -> Image.Image:
    scale = 4
    S = size * scale
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pad = int(S * .055)
    rounded(d, (pad, pad, S-pad, S-pad), int(S*.19), (21, 28, 39, 255), (219, 174, 75, 255), max(2, int(S*.018)))
    tiles = [
        (0.17, 0.59, 0.43, 0.82),
        (0.31, 0.38, 0.63, 0.68),
        (0.48, 0.18, 0.84, 0.55),
    ]
    fills = [(39, 71, 91, 255), (47, 98, 122, 255), (60, 132, 157, 255)]
    for i, (l,t,r,b) in enumerate(tiles):
        box = tuple(int(v*S) for v in (l,t,r,b))
        rounded(d, box, int(S*.045), fills[i], (244, 207, 116, 255), max(2, int(S*.015)))
        inset = int(S*.025)
        d.line((box[0]+inset, box[3]-inset, box[2]-inset, box[1]+inset), fill=(247, 233, 191, 255), width=max(2,int(S*.018)))
        q=max(2,int(S*.026)); x=box[2]-inset-q; y=box[1]+inset
        d.rectangle((x,y,x+q,y+q), fill=(255, 246, 188, 255))
    y=int(S*.865); x1=int(S*.18); x2=int(S*.82)
    d.rounded_rectangle((x1,y,x2,y+max(3,int(S*.025))), radius=max(2,int(S*.012)), fill=(219,174,75,255))
    return im.resize((size,size), Image.Resampling.LANCZOS)

def main():
    RES.mkdir(parents=True, exist_ok=True)
    preview = render(256)
    png = RES / "H3TierExport-icon.png"
    preview.save(png, "PNG")
    ico = RES / "H3TierExport.ico"
    preview.save(ico, format="ICO", sizes=[(s,s) for s in (16,20,24,32,40,48,64,96,128,256)])
    print(json.dumps({
        "ico": str(ico), "ico_bytes": ico.stat().st_size, "ico_sha256": hashlib.sha256(ico.read_bytes()).hexdigest(),
        "preview": str(png), "preview_bytes": png.stat().st_size, "preview_sha256": hashlib.sha256(png.read_bytes()).hexdigest()
    }, ensure_ascii=False))

if __name__ == "__main__":
    main()
