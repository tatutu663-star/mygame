#!/usr/bin/env python3
"""สร้าง launcher.json สำหรับระบบอัปเดต Launcher เอง (รันโดย workflow)"""
import argparse
import hashlib
import json
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--exe", required=True)
ap.add_argument("--version", required=True)
ap.add_argument("--repo", required=True, help="USER/REPO")
ap.add_argument("--min-supported", default="1.0.0")
ap.add_argument("--notes", default="")
ap.add_argument("--out", default="dist")
a = ap.parse_args()

exe = Path(a.exe)
data = exe.read_bytes()
info = {
    "version": a.version,
    "min_supported": a.min_supported,
    # ชี้ไปที่ไฟล์ของเวอร์ชันนั้นๆ โดยตรง (ไม่เปลี่ยนแปลง) + ล็อก hash
    "url": f"https://github.com/{a.repo}/releases/download/launcher-{a.version}/{exe.name}",
    "sha256": hashlib.sha256(data).hexdigest(),
    "size": len(data),
    "notes": a.notes,
}
out = Path(a.out)
out.mkdir(parents=True, exist_ok=True)
(out / "launcher.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), "utf-8")
print(f"launcher.json v{a.version} -> {out / 'launcher.json'}")
