@echo off
rem ทดลอง build .exe บนเครื่องตัวเอง (ของจริงให้ใช้ GitHub Actions: แท็บ Actions -> Build launcher)
rem ต้องสร้างไฟล์ launcher\build_info.py ก่อน เช่น:
rem   REPO = "user/repo"
rem   BRANCH = "main"
rem   VERSION = "0.0.1"
rem รูปไอคอนจะถูกเข้ารหัสฝังใน .exe ด้วย tools\pack_assets.py (ไม่ต้องแนบโฟลเดอร์ icons)
pip install -r launcher\requirements.txt pyinstaller
python tools\pack_assets.py
pyinstaller --onefile --icon icon.ico --hidden-import assets_blob --windowed --name MyGameLauncher --add-data "config.json;." --distpath dist --paths launcher launcher\app.py
echo ได้ไฟล์: dist\MyGameLauncher.exe
