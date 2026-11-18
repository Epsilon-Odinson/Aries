"""Regenerate the purple nebula artwork for the Aries veil.

Run from production/:  python3 tools/generate_nebula.py
Writes web/public/nebula.jpg (used only to regenerate src/nebulaImage.js,
which embeds the image as a data URI so the live site never depends on an
external image host). Requires Pillow: pip install pillow.
"""
import random
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

random.seed(7)
W, H = 1400, 900
img = Image.new("RGB", (W, H), (13, 0, 16))
layer = Image.new("RGB", (W, H), (0, 0, 0))
d = ImageDraw.Draw(layer)
palettes = [(123, 47, 255), (88, 28, 180), (168, 85, 247),
            (64, 16, 120), (217, 164, 65), (147, 51, 234)]
for _ in range(90):
    x, y = random.randint(0, W), random.randint(0, H)
    r = random.randint(60, 260)
    c = random.choice(palettes)
    a = random.uniform(0.25, 0.7)
    d.ellipse([x - r, y - r, x + r, y + r],
              fill=tuple(int(v * a) for v in c))
layer = layer.filter(ImageFilter.GaussianBlur(60))
img = Image.blend(img, Image.blend(img, layer, 0.85), 0.9)
d = ImageDraw.Draw(img)
for _ in range(700):
    x, y = random.randint(0, W), random.randint(0, H)
    r = random.choice([1, 1, 1, 2])
    b = random.randint(120, 255)
    d.ellipse([x - r, y - r, x + r, y + r], fill=(b, b, min(255, b + 20)))
vig = Image.new("L", (W, H), 0)
dv = ImageDraw.Draw(vig)
dv.ellipse([-W * 0.25, -H * 0.25, W * 1.25, H * 1.25], fill=255)
vig = vig.filter(ImageFilter.GaussianBlur(120))
img = Image.composite(img, Image.new("RGB", (W, H), (5, 0, 8)), vig)
out = Path(__file__).resolve().parent.parent / "web" / "public" / "nebula.jpg"
out.parent.mkdir(parents=True, exist_ok=True)
img.save(out, quality=88)
print("wrote", out)
