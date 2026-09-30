"""Draw the app icon (.ico) and installer artwork (.bmp) with Pillow."""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

ACCENT = (124, 108, 255)
ACCENT2 = (57, 208, 200)
DARK = (20, 22, 28)


def gradient(w, h, c1, c2):
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            t = (x / max(1, w - 1) + y / max(1, h - 1)) / 2
            px[x, y] = tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))
    return img


def icon_image(size=256):
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([s * 0.03, s * 0.03, s * 0.97, s * 0.97], radius=s * 0.22, fill=255)
    img.paste(gradient(s, s, ACCENT, ACCENT2), (0, 0), mask)
    d = ImageDraw.Draw(img)
    left, right, top, bot = s * 0.16, s * 0.84, s * 0.24, s * 0.78
    kw = (right - left) / 5
    for i in range(5):
        d.rounded_rectangle([left + i * kw + s * 0.008, top, left + (i + 1) * kw - s * 0.008, bot],
                            radius=s * 0.03, fill=(255, 255, 255, 255))
    for i in (0, 1, 3):
        cx = left + (i + 1) * kw
        d.rounded_rectangle([cx - kw * 0.3, top, cx + kw * 0.3, top + (bot - top) * 0.58],
                            radius=s * 0.02, fill=(27, 30, 38, 255))
    return img


def _font(size):
    for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def wizard_bitmap(path):
    # MUI welcome/finish page: 164 x 314
    img = gradient(164, 314, (40, 34, 90), DARK)
    ic = icon_image(96)
    img.paste(ic, (34, 60), ic)
    d = ImageDraw.Draw(img)
    f = _font(15)
    for i, line in enumerate(("Modern", "MIDI Player")):
        w = d.textlength(line, font=f)
        d.text(((164 - w) / 2, 175 + i * 20), line, font=f, fill=(230, 233, 242))
    for i, c in enumerate([(255, 92, 122), (255, 194, 76), (127, 227, 107), (67, 167, 255), (185, 140, 255)]):
        x = 30 + i * 22
        h = [30, 50, 38, 60, 44][i]
        d.rounded_rectangle([x, 290 - h, x + 12, 290], radius=3, fill=c)
    img.save(path, "BMP")


def header_bitmap(path):
    # MUI header: 150 x 57
    img = Image.new("RGB", (150, 57), (255, 255, 255))
    ic = icon_image(44)
    img.paste(ic, (100, 6), ic)
    img.save(path, "BMP")


def main(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    big = icon_image(256)
    big.save(os.path.join(out_dir, "icon.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                                                      (128, 128), (256, 256)])
    wizard_bitmap(os.path.join(out_dir, "wizard.bmp"))
    header_bitmap(os.path.join(out_dir, "header.bmp"))
    big.save(os.path.join(out_dir, "icon.png"))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "build"))
