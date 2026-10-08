# MyGame Launcher (repo เดียว · ตั้งค่า 4 ขั้นตอน · ฟรี)

ใช้ GitHub เป็นเซิร์ฟเวอร์: ไฟล์เกมอยู่ใน Releases, ข่าว/สถานะอยู่ในโฟลเดอร์ `site/`
**ไม่ต้องมีกุญแจ ไม่ต้องตั้ง Secret ไม่ต้องเปิด Pages ไม่ต้องแก้ URL**

## ตั้งค่าครั้งแรก

1. **สร้าง repo บน GitHub แบบ Public** แล้ว push ทั้งโฟลเดอร์นี้ขึ้นไป
   (ต้อง Public ไม่งั้นผู้เล่นโหลดไฟล์ไม่ได้)
2. **แก้ `config.json`** — ปกติแก้แค่ 2 ค่า:
   - `app_name` ชื่อเกม
   - `game_exe` ไฟล์ที่ใช้เปิดเกม (path ภายในโฟลเดอร์เกม)
3. **Build Launcher:** แท็บ **Actions → Build launcher → Run workflow** → ใส่เวอร์ชัน `1.0.0`
   พอเสร็จ (~3 นาที) เปิดหน้า run จะเห็นลิงก์ดาวน์โหลด `.exe` ใน Summary
   → ลิงก์นี้ใช้แจกผู้เล่นได้ถาวร
4. **ออกเกมเวอร์ชันแรก:** Releases → **Draft a new release**
   - tag: `stable-1.0.0`
   - ลากไฟล์เกมขึ้นไปแนบ → กด **Publish release** แล้วรอ workflow ~1 นาที

จบ — ผู้เล่นเปิด Launcher แล้วโหลดเกมได้เลย

## ออกอัปเดตเกม

Releases → Draft a new release → tag `stable-1.1.0` (หรือ `beta-1.2.0`) → แนบไฟล์ → Publish

- แนบ **`.zip`** = แพ็กเกม (โครงสร้างในซิปคือโครงสร้างโฟลเดอร์เกมจริง เช่น `data/char.pak`)
  แบ่งหลายซิปได้ เช่น `base.zip`, `audio.zip`
- แนบไฟล์อื่น = ไฟล์เดี่ยววางที่ราก เช่น `game.exe`
- ชื่อขึ้นต้น `_` = ไม่ใช้ (ใส่โน้ตได้)
- **แพตช์:** ไฟล์ซ้ำข้ามซิป ซิปที่ชื่อเรียงทีหลังจะทับ (เช่น `base.zip` ← `patch.zip`)
- ผู้เล่นจะโหลด **เฉพาะไฟล์ที่เปลี่ยน** (เทียบ hash รายไฟล์)
- ไฟล์ที่หายไปจาก Release ใหม่ Launcher ลบจากเครื่องผู้เล่นให้เอง
- อัปโหลดไฟล์ใหญ่ใช้ **Draft** ไว้ก่อน แล้วค่อย Publish (ยังไม่มีใครเห็น)
- ถ้าเพิ่มไฟล์ใน Release ที่ Publish ไปแล้ว → Actions → Publish game → Run workflow
- ถอยเวอร์ชัน: ลบ Release ใหม่ทิ้ง (ระบบใช้ Release ล่าสุดของแต่ละช่องทางอัตโนมัติ)

## ใช้งานประจำ

| ต้องการ | ทำอย่างไร |
|---|---|
| ปิดปรับปรุง | แก้ `site/status.json` → `{"maintenance": true, "message": "..."}` แล้ว commit |
| เพิ่มข่าว/แบนเนอร์ | แก้ `site/news.json` + วางรูป PNG 900×300 ใน `site/banners/` แล้ว commit |
| ลบไฟล์เฉพาะจากเครื่องผู้เล่น | ใส่ path ใน `delete.txt` (บรรทัดละไฟล์) แล้วรัน Actions → Publish game |
| ออก Launcher เวอร์ชันใหม่ | Actions → Build launcher → ใส่เวอร์ชันใหม่ (ผู้เล่นอัปเดตอัตโนมัติ) |
| บังคับอัปเดต Launcher | แก้ `min_launcher_version` ใน `config.json` ก่อน build |
| เพิ่มช่องทาง | เพิ่มชื่อใน `channels` ของ `config.json` แล้ว build Launcher ใหม่ |

ข่าว/สถานะมีผลภายในไม่กี่นาทีหลัง commit (ไม่ต้องรัน workflow)

## โครงสร้าง

```
config.json            ค่าเดียวที่ต้องแก้ (ชื่อเกม, ไฟล์เปิดเกม, ช่องทาง)
launcher/              ซอร์ส Launcher (Tkinter)
tools/                 สคริปต์ที่ workflow เรียกใช้
site/                  news.json · status.json · banners/   (อ่านผ่าน raw.githubusercontent.com)
delete.txt             ไฟล์ที่อยากให้ลบจากเครื่องผู้เล่น (ถ้ามี)
.github/workflows/     build-launcher.yml · publish-game.yml
```

Release ชื่อ `meta` (สร้างให้อัตโนมัติ **ห้ามลบ**) เก็บ `manifest-<ช่องทาง>.json` และ `launcher.json`
ที่ Launcher อ่าน

## ความปลอดภัย

- ลิงก์ทั้งหมดเป็น HTTPS และ manifest ล็อก **SHA-256 ของทุกแพ็กและทุกไฟล์** — Launcher ตรวจก่อนวางไฟล์
  ทุกครั้ง ถ้าใครสลับไฟล์ใน Release ภายหลังจะถูกปฏิเสธ; กัน zip-slip / zip-bomb
- ความเชื่อถือขึ้นกับสิทธิ์เขียนใน repo (ใครแก้ repo ได้ = ออกอัปเดตได้) → เปิด 2FA และแจกสิทธิ์เฉพาะคนที่ไว้ใจ
- Launcher อัปเดตตัวเอง: โหลด → ตรวจ hash → สำรองตัวเก่าเป็น `.old` → ถ้าตัวใหม่ crash ใน 6 วินาทีจะย้อนกลับเอง
- ล็อกอยู่ที่ `%APPDATA%\<app_name>\launcher.log`

## ข้อจำกัด

- ไฟล์ใน Release ต้อง < 2 GB ต่อไฟล์ (เกมใหญ่ให้แบ่งหลาย zip)
- workflow ต้องโหลดแพ็กใหม่ไปคำนวณ hash บน runner (ดิสก์ ~14 GB) — แพ็กที่เคยวิเคราะห์แล้วจะถูก cache
- GitHub ไม่ใช่ CDN เต็มรูปแบบ ถ้าผู้เล่นเยอะอาจโดนจำกัดแบนด์วิดท์
- `.exe` ไม่ได้เซ็นโค้ด → Windows SmartScreen / แอนตี้ไวรัสบางตัวอาจเตือน
- Build เป็น Windows (workflow ใช้ `windows-latest`)
- ใช้แจกจ่ายเกม/แอปของคุณเองหรือที่คุณมีสิทธิ์เท่านั้น

## ทดสอบในเครื่อง (ไม่บังคับ)

```
# สร้าง manifest จากโฟลเดอร์จำลอง  releases/stable-1.0.0/<ไฟล์...>
python tools/build_manifest.py --local-releases releases --asset-base http://localhost:8000/releases/ --out meta
# เปิดเซิร์ฟเวอร์ที่รากโฟลเดอร์ (ให้มี meta/ releases/ site/) แล้วรัน Launcher จากซอร์ส
python -m http.server 8000
set MYGAME_REPO=test/test
set MYGAME_ALLOW_HTTP=1
set MYGAME_META_BASE=http://localhost:8000/meta/
set MYGAME_RAW_BASE=http://localhost:8000/site/
pip install -r launcher/requirements.txt
python launcher/app.py
```
