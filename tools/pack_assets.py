#!/usr/bin/env python3
"""เข้ารหัสรูปของ Launcher (icons/*.png และ icon.ico) แล้วเขียนเป็น launcher/assets_blob.py

รันเมื่อแก้/เพิ่มรูปไอคอน:   python tools/pack_assets.py
(GitHub Actions รันให้เองตอน build ถ้ายังมีโฟลเดอร์ icons/ อยู่)

ถ้าไม่มี icons/ และ icon.ico จะไม่แตะ assets_blob.py ที่มีอยู่ — จึงลบ icons/ ออกจาก repo ได้
หลังจากสร้าง assets_blob.py แล้ว (คนเปิด repo จะได้ไม่เห็นรูปต้นฉบับ)
"""
import base64
import io
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "launcher"))
import assetbox  # noqa: E402

ICON_DIR = ROOT / "icons"
EXTS = (".png", ".webp", ".ico")
OUT = ROOT / "launcher" / "assets_blob.py"


def png_bytes(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.convert("RGBA").save(buf, "PNG", optimize=True)
    return buf.getvalue()


def main() -> int:
    try:  # Windows (cp1252) พิมพ์ภาษาไทยไม่ได้ ทำให้สคริปต์ล้ม
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    items = {}
    if ICON_DIR.is_dir():
        for f in sorted(ICON_DIR.iterdir()):
            if f.suffix.lower() in EXTS and f.is_file():
                with Image.open(f) as im:
                    items[f.stem.lower()] = png_bytes(im)
    ico = ROOT / "icon.ico"
    if ico.is_file():
        with Image.open(ico) as im:
            sizes = sorted(getattr(im, "ico", None).sizes()) if hasattr(im, "ico") else [im.size]
            if hasattr(im, "ico"):
                im.size = sizes[-1]  # เลือกขนาดใหญ่สุดในไฟล์ .ico
            items["app"] = png_bytes(im)
    if not items:
        print("ไม่พบ icons/ หรือ icon.ico — ใช้ assets_blob.py เดิม (ถ้ามี)")
        return 0
    lines = ['"""สร้างอัตโนมัติโดย tools/pack_assets.py — ห้ามแก้เอง (รูปถูกเข้ารหัสไว้)"""', "BLOB = {"]
    for name, data in items.items():
        b64 = base64.b64encode(assetbox.seal(data)).decode("ascii")
        lines.append(f'    "{name}": (')
        lines += [f'        "{b64[i:i + 100]}"' for i in range(0, len(b64), 100)]
        lines.append("    ),")
    lines.append("}")
    OUT.write_text("\n".join(lines) + "\n", "utf-8")
    print(f"เขียน {OUT.relative_to(ROOT)}: {', '.join(items)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
