"""Generate launcher icons (green rounded square with a $ glyph).

Run once with Pillow installed; the PNGs are committed so CI doesn't need PIL:
    python3 make_icons.py
"""

import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "app", "src", "main", "res")

SIZES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
BG_TOP = (46, 160, 108)
BG_BOTTOM = (23, 30, 46)
BASE = 512


def make_base():
    img = Image.new("RGBA", (BASE, BASE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # vertical gradient background
    grad = Image.new("RGBA", (1, BASE))
    for y in range(BASE):
        t = y / (BASE - 1)
        grad.putpixel((0, y), tuple(
            int(BG_TOP[i] * (1 - t) + BG_BOTTOM[i] * t) for i in range(3)) + (255,))
    grad = grad.resize((BASE, BASE))
    mask = Image.new("L", (BASE, BASE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([8, 8, BASE - 8, BASE - 8], radius=110, fill=255)
    img.paste(grad, (0, 0), mask)
    # $ glyph
    font = ImageFont.load_default(size=340)
    text = "$"
    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((BASE - w) / 2 - bbox[0], (BASE - h) / 2 - bbox[1]), text,
              font=font, fill=(255, 255, 255, 255))
    return img


def main():
    base = make_base()
    for dpi, size in SIZES.items():
        out_dir = os.path.join(RES, f"mipmap-{dpi}")
        os.makedirs(out_dir, exist_ok=True)
        base.resize((size, size), Image.LANCZOS).save(os.path.join(out_dir, "ic_launcher.png"))
        print(f"mipmap-{dpi}/ic_launcher.png ({size}px)")


if __name__ == "__main__":
    main()
