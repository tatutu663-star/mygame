"""งานภาพของ Launcher (ใช้ Pillow ล้วน ไม่ผูกกับ Tkinter เพื่อให้ทดสอบแยกได้)

- make_fallback_bg : พื้นหลังสำรองที่สร้างเองเมื่อยังไม่มี site/background.png
- compose_background : รวมพื้นหลัง + แสงเงา + การ์ดข่าวโปร่งแสง เป็นภาพเดียวขนาดหน้าต่าง
- make_thumb : ย่อรูปข่าวให้พอดีการ์ด (มุมบนมน)
"""
from __future__ import annotations

from PIL import Image, ImageChops, ImageDraw, ImageFilter

W, H = 1280, 720
CARD = (57, 365, 441, 658)   # x1, y1, x2, y2 ของการ์ดข่าว
CARD_RADIUS = 14
THUMB_SIZE = (CARD[2] - CARD[0], 176)


def cover(img: Image.Image, w: int, h: int) -> Image.Image:
    """ย่อ/ครอปให้เต็มกรอบ (เหมือน CSS background-size: cover)"""
    img = img.convert("RGB")
    scale = max(w / img.width, h / img.height)
    nw, nh = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    img = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def make_fallback_bg(w: int = W, h: int = H) -> Image.Image:
    """ไล่สีฟ้าอมเขียว + แสงฟุ้ง + เส้นเฉียงบางๆ"""
    top, bottom = (168, 205, 220), (38, 92, 116)
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / (h - 1)
        d.line([(0, y), (w, y)], fill=tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))

    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse((w * 0.55, -h * 0.25, w * 1.15, h * 0.65), fill=(255, 255, 255, 120))
    g.ellipse((-w * 0.15, h * 0.1, w * 0.35, h * 0.8), fill=(255, 255, 255, 50))
    glow = glow.filter(ImageFilter.GaussianBlur(80))
    img = Image.alpha_composite(img.convert("RGBA"), glow)

    lines = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lines)
    for i, (x, a) in enumerate(((0.30, 40), (0.46, 28), (0.62, 36), (0.78, 22))):
        ld.line([(w * x, h), (w * x + 380, -40)], fill=(255, 255, 255, a), width=2 + i % 2)
    return Image.alpha_composite(img, lines.filter(ImageFilter.GaussianBlur(1))).convert("RGB")


def _rounded_mask(size, radius, corners=(True, True, True, True)) -> Image.Image:
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius,
                                        fill=255, corners=corners)
    return m


def compose_background(bg: Image.Image | None) -> Image.Image:
    """พื้นหลังเต็มหน้าต่าง + เงาไล่ระดับ (ให้ตัวหนังสืออ่านง่าย) + การ์ดข่าวแบบกระจกฝ้า"""
    base = cover(bg, W, H) if bg is not None else make_fallback_bg()

    # เงาด้านล่าง/ซ้าย (แยกเลเยอร์แล้วค่อยซ้อน ไม่ให้เส้นทับกันจนเกิดรอยต่อ)
    img = base.convert("RGBA")
    for horizontal, span, strength, power in ((False, H // 2, 120, 1.6), (True, 560, 70, 2)):
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        if horizontal:   # ไล่เงาจากขอบซ้าย
            for x in range(span):
                ld.line([(x, 0), (x, H)], fill=(8, 24, 34, int(strength * (1 - x / span) ** power)))
        else:            # ไล่เงาขึ้นจากขอบล่าง
            for y in range(H - span, H):
                ld.line([(0, y), (W, y)],
                        fill=(8, 24, 34, int(strength * ((y - (H - span)) / span) ** power)))
        img = Image.alpha_composite(img, layer)

    # การ์ดข่าว: เบลอพื้นหลังเฉพาะบริเวณการ์ด + ขาวโปร่ง + ขอบบาง
    x1, y1, x2, y2 = CARD
    size = (x2 - x1, y2 - y1)
    region = img.crop(CARD).filter(ImageFilter.GaussianBlur(10)).convert("RGBA")
    region = Image.alpha_composite(region, Image.new("RGBA", size, (255, 255, 255, 38)))
    mask = _rounded_mask(size, CARD_RADIUS)
    img.paste(region, (x1, y1), mask)
    border = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(border).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=CARD_RADIUS,
                                             outline=(255, 255, 255, 60), width=1)
    img.alpha_composite(border, (x1, y1))
    return img.convert("RGB")


def make_thumb(path, size=THUMB_SIZE, radius=CARD_RADIUS) -> Image.Image:
    """รูปข่าวขนาดเท่าด้านบนของการ์ด มุมบนมน มุมล่างเหลี่ยม"""
    with Image.open(path) as im:
        im = cover(im, *size).convert("RGBA")
    mask = _rounded_mask(size, radius, corners=(True, True, False, False))
    im.putalpha(ImageChops.multiply(im.getchannel("A"), mask))
    return im
