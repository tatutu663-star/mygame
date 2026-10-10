"""กล่องเก็บรูปของ Launcher — เข้ารหัสรูปทั้งหมดไว้ ไม่ให้เปิดดู/แก้ด้วยโปรแกรมดูรูปธรรมดา

- รูปที่ฝังมากับตัวโปรแกรม (ไอคอนปุ่ม, ไอคอนแอป) อยู่ใน assets_blob.py ซึ่ง PyInstaller รวมเข้า .exe
  เป็นโค้ด ไม่ได้แตกเป็นไฟล์รูปวางไว้ในเครื่องผู้เล่น  (สร้างไฟล์นั้นด้วย tools/pack_assets.py)
- รูปที่โหลดมาเก็บแคช (พื้นหลัง, แบนเนอร์ข่าว) ถูกเข้ารหัสเป็นไฟล์ .dat

หมายเหตุ: นี่คือการกันคนทั่วไป (ดับเบิลคลิกเปิดดู / ลากไปแก้) ไม่ใช่ความปลอดภัยระดับสูง
เพราะกุญแจต้องอยู่ในตัวโปรแกรมเอง คนที่ถอดโค้ด Python ได้ยังเอาออกมาได้
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import os
from pathlib import Path

from PIL import Image

MAGIC = b"TTB1"
_NONCE = 12
_TAG = 16
# กุญแจแยกเป็นหลายท่อนไม่ให้เห็นตรงๆ เวลา grep ใน exe — เปลี่ยนค่าได้ แต่ต้องรัน tools/pack_assets.py ใหม่
_K = (b"\x54\x61\x54\x75", b"-launcher-", bytes(range(16, 40)), b"\xa7\x3c\x5e\x91\x0d\xe2\x6b\x48")
_KEY = hashlib.sha256(b"".join(_K)).digest()


def _stream(nonce: bytes, n: int) -> bytes:
    out = bytearray()
    ctr = 0
    while len(out) < n:
        out += hashlib.blake2b(nonce + ctr.to_bytes(8, "big"), key=_KEY, digest_size=64).digest()
        ctr += 1
    return bytes(out[:n])


def seal(data: bytes) -> bytes:
    nonce = os.urandom(_NONCE)
    ks = _stream(nonce, len(data))
    ct = (int.from_bytes(data, "big") ^ int.from_bytes(ks, "big")).to_bytes(len(data), "big") if data else b""
    tag = hmac.new(_KEY, MAGIC + nonce + ct, hashlib.sha256).digest()[:_TAG]
    return MAGIC + nonce + ct + tag


def unseal(blob: bytes) -> bytes | None:
    """คืน None ถ้าไม่ใช่รูปแบบของเรา หรือข้อมูลถูกแก้/เสีย"""
    if len(blob) < len(MAGIC) + _NONCE + _TAG or blob[:len(MAGIC)] != MAGIC:
        return None
    nonce = blob[len(MAGIC):len(MAGIC) + _NONCE]
    ct = blob[len(MAGIC) + _NONCE:-_TAG]
    tag = blob[-_TAG:]
    want = hmac.new(_KEY, MAGIC + nonce + ct, hashlib.sha256).digest()[:_TAG]
    if not hmac.compare_digest(tag, want):
        return None
    if not ct:
        return b""
    ks = _stream(nonce, len(ct))
    return (int.from_bytes(ct, "big") ^ int.from_bytes(ks, "big")).to_bytes(len(ct), "big")


# ---------------------------------------------------------------- ไฟล์แคชบนดิสก์
def write_sealed(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(seal(data))
    os.replace(tmp, path)


def read_sealed(path: Path) -> bytes | None:
    try:
        return unseal(Path(path).read_bytes())
    except OSError:
        return None


def read_image(path) -> Image.Image:
    """เปิดรูปจากไฟล์แคชที่เข้ารหัส (ถ้าไม่ใช่ไฟล์เข้ารหัสจะลองเปิดเป็นรูปปกติ — ไว้ใช้ตอนพัฒนา)"""
    data = read_sealed(Path(path))
    if data is None:
        with Image.open(path) as im:
            im.load()
            return im.copy()
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        return im.copy()


# ---------------------------------------------------------------- รูปที่ฝังมากับโปรแกรม
def _blob() -> dict:
    try:
        import assets_blob  # สร้างโดย tools/pack_assets.py
        return assets_blob.BLOB
    except (ImportError, AttributeError):
        return {}


def embedded_bytes(name: str) -> bytes | None:
    b64 = _blob().get(name)
    if not b64:
        return None
    try:
        return unseal(base64.b64decode(b64))
    except ValueError:
        return None


def embedded_image(name: str) -> Image.Image | None:
    data = embedded_bytes(name)
    if data is None:
        return None
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            return im.copy()
    except (OSError, ValueError):
        return None
