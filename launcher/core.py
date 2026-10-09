"""ตรรกะหลักของ Launcher: เทียบไฟล์, ดาวน์โหลด, ตรวจ hash, อัปเดตตัวเอง
ไม่ผูกกับ GUI เพื่อให้ทดสอบแยกได้"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import zipfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

import requests
import config

CHUNK = 256 * 1024
TMP_DIR = ".launcher_tmp"  # ที่พักไฟล์ zip ระหว่างโหลด/แตกไฟล์
_session = requests.Session()
_session.headers["User-Agent"] = f"{config.APP_NAME}-Launcher/{config.LAUNCHER_VERSION}"


class LauncherError(Exception):
    """ข้อผิดพลาดที่แสดงให้ผู้ใช้อ่านได้"""


class NotFound(LauncherError):
    """ไม่พบไฟล์บนเซิร์ฟเวอร์ (404)"""


class SelfUpdateCrashed(LauncherError):
    """Launcher ตัวใหม่เปิดไม่ขึ้น ถูกย้อนกลับไปใช้ตัวเก่าแล้ว"""


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------- พื้นฐาน
def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", str(Path.home())))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    p = base / config.APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def log(msg) -> None:
    try:
        p = data_dir() / "launcher.log"
        if p.exists() and p.stat().st_size > 512 * 1024:
            p.write_text("")
        with open(p, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg) + "\n")
    except OSError:
        pass


def parse_version(v) -> tuple:
    parts = []
    for p in str(v).split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits or 0))
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts)


def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def fmt_eta(sec) -> str:
    if sec is None:
        return "--"
    sec = int(sec)
    if sec >= 3600:
        return f"{sec // 3600} ชม. {sec % 3600 // 60} นาที"
    if sec >= 60:
        return f"{sec // 60} นาที {sec % 60} วินาที"
    return f"{sec} วินาที"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def check_url(url) -> str:
    if not isinstance(url, str):
        raise LauncherError("URL ไม่ถูกต้อง")
    if url.startswith("https://"):
        return url
    if config.ALLOW_INSECURE_HTTP and url.startswith("http://"):
        return url
    raise LauncherError(f"ปฏิเสธ URL ที่ไม่ใช่ HTTPS: {url}")


def safe_join(root: Path, rel: str) -> Path:
    """กัน path traversal (../) และ path แบบ absolute จาก manifest"""
    if not isinstance(rel, str) or not rel or os.path.isabs(rel) or ".." in Path(rel).parts:
        raise LauncherError(f"path ไม่ปลอดภัยใน manifest: {rel!r}")
    root_r = root.resolve()
    p = (root_r / rel).resolve()
    try:
        p.relative_to(root_r)
    except ValueError:
        raise LauncherError(f"path หลุดออกนอกโฟลเดอร์ติดตั้ง: {rel!r}")
    return p


# ---------------------------------------------------------------- ตั้งค่าผู้ใช้
@dataclass
class Settings:
    install_dir: str = ""
    channel: str = config.DEFAULT_CHANNEL
    close_on_launch: bool = True
    bad_launcher_version: str = ""

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        try:
            raw = json.loads((data_dir() / "settings.json").read_text("utf-8"))
            for k in asdict(s):
                if k in raw:
                    setattr(s, k, raw[k])
        except (OSError, ValueError):
            pass
        if not s.install_dir:
            s.install_dir = str(data_dir() / "game")
        return s

    def save(self) -> None:
        (data_dir() / "settings.json").write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), "utf-8")


# ---------------------------------------------------------------- เครือข่าย
def http_get(url: str, bust_cache: bool = False, timeout: int = 15) -> bytes:
    check_url(url)
    if bust_cache:  # raw.githubusercontent.com แคชไว้ ~5 นาที ใส่ query กันแคช
        url += ("&" if "?" in url else "?") + f"t={int(time.time())}"
    last = None
    for attempt in range(3):
        try:
            r = _session.get(url, timeout=timeout)
            if r.status_code == 404:
                raise NotFound(f"ไม่พบไฟล์บนเซิร์ฟเวอร์: {url}")
            r.raise_for_status()
            return r.content
        except NotFound:
            raise
        except requests.RequestException as e:
            last = e
            time.sleep(1 + attempt)
    raise LauncherError(f"เชื่อมต่อไม่ได้: {url} ({last})")


def fetch_json(url: str, bust_cache: bool = False) -> dict:
    data = http_get(url, bust_cache=bust_cache)
    try:
        obj = json.loads(data.decode("utf-8"))
    except ValueError:
        raise LauncherError(f"ไฟล์ JSON เสียหาย: {url}")
    if not isinstance(obj, dict):
        raise LauncherError(f"รูปแบบ JSON ไม่ถูกต้อง: {url}")
    return obj


def fetch_status() -> dict:
    """status.json ไม่จำเป็นต้องมี — ถ้าไม่มีถือว่าไม่ได้ปิดปรับปรุง"""
    try:
        return fetch_json(config.RAW_BASE + "status.json", bust_cache=True)
    except NotFound:
        return {}


def fetch_news() -> dict:
    try:
        return fetch_json(config.RAW_BASE + "news.json", bust_cache=True)
    except NotFound:
        return {"items": []}


def fetch_banner(item: dict) -> Path | None:
    """โหลดรูปแบนเนอร์ลงแคช (ชื่อไฟล์แคชผูกกับ hash ของเนื้อหา จึงไม่โหลดซ้ำถ้ารูปไม่เปลี่ยน)"""
    rel = item.get("image")
    if not rel or not isinstance(rel, str) or ".." in Path(rel).parts:
        return None
    data = http_get(config.RAW_BASE + rel, bust_cache=True)
    cache = data_dir() / "banners"
    cache.mkdir(exist_ok=True)
    dest = cache / f"{hashlib.sha256(data).hexdigest()[:24]}.png"
    if not dest.exists():
        dest.write_bytes(data)
    return dest


def _need(cond, msg="รูปแบบ manifest ไม่ถูกต้อง"):
    if not cond:
        raise LauncherError(msg)


def validate_manifest(m: dict) -> dict:
    """manifest: packs = ไฟล์ที่อยู่บน Release (zip หรือไฟล์เดี่ยว), files = ไฟล์จริงในเกม
    แต่ละไฟล์ระบุว่ามาจากแพ็กไหน"""
    try:
        _need(isinstance(m["game_version"], str))
        packs = {}
        for p in m["packs"]:
            _need(isinstance(p["id"], str) and p["id"] not in packs)
            _need(p["kind"] in ("zip", "file"))
            _need(isinstance(p["size"], int) and p["size"] > 0)
            _need(isinstance(p["sha256"], str) and len(p["sha256"]) == 64)
            check_url(p["url"])
            packs[p["id"]] = p
        _need(isinstance(m["files"], list))
        for it in m["files"]:
            _need(isinstance(it["path"], str))
            _need(isinstance(it["size"], int) and it["size"] >= 0)
            _need(isinstance(it["sha256"], str) and len(it["sha256"]) == 64)
            _need(it["pack"] in packs)
            if packs[it["pack"]]["kind"] == "file":
                _need(it["sha256"] == packs[it["pack"]]["sha256"])
        _need(isinstance(m.get("delete", []), list))
    except (KeyError, TypeError):
        raise LauncherError("รูปแบบ manifest ไม่ถูกต้อง")
    return m


def fetch_manifest(channel: str) -> dict:
    if channel not in config.CHANNELS:
        raise LauncherError(f"ไม่รู้จักช่องทาง: {channel}")
    try:
        return validate_manifest(fetch_json(f"{config.META_BASE}manifest-{channel}.json"))
    except NotFound:
        raise LauncherError("ยังไม่มีเกมให้ติดตั้งในช่องทางนี้ (ผู้ดูแลยังไม่ได้ออก Release)")


# ---------------------------------------------------------------- สถานะการติดตั้ง
class InstallState:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / ".launcher_state.json"
        self.version = ""
        self.channel = ""
        self.files: dict = {}

    @classmethod
    def load(cls, root: Path) -> "InstallState":
        st = cls(root)
        try:
            raw = json.loads(st.path.read_text("utf-8"))
            st.version = raw.get("version", "")
            st.channel = raw.get("channel", "")
            st.files = raw.get("files", {})
        except (OSError, ValueError):
            pass
        return st

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": self.version, "channel": self.channel,
                                   "files": self.files}), "utf-8")
        os.replace(tmp, self.path)


def plan_update(manifest: dict, root: Path, state: InstallState,
                full_verify: bool = False, on_scan=None) -> list:
    """คืนรายการไฟล์ที่ต้องโหลด (เทียบขนาด+mtime ก่อน, ถ้าสงสัยค่อยคำนวณ hash)"""
    todo = []
    files = manifest["files"]
    for i, item in enumerate(files):
        if on_scan:
            on_scan(i, len(files))
        dest = safe_join(root, item["path"])
        ok = False
        if dest.is_file():
            st = dest.stat()
            if st.st_size == item["size"]:
                c = state.files.get(item["path"])
                if (not full_verify and c and c.get("sha256") == item["sha256"]
                        and c.get("size") == st.st_size and c.get("mtime_ns") == st.st_mtime_ns):
                    ok = True
                else:
                    ok = sha256_file(dest) == item["sha256"]
        if not ok:
            todo.append(item)
    if on_scan:
        on_scan(len(files), len(files))
    return todo


@dataclass
class Job:
    pack: dict
    items: list  # ไฟล์ที่ต้องดึงจากแพ็กนี้


def build_jobs(manifest: dict, todo: list) -> list:
    packs = {p["id"]: p for p in manifest["packs"]}
    by: dict = {}
    for it in todo:
        by.setdefault(it["pack"], []).append(it)
    return [Job(packs[k], v) for k, v in by.items()]


def check_disk_space(root: Path, jobs: list, workers: int = 3) -> None:
    need = sum(it["size"] for j in jobs for it in j.items)  # ไฟล์ที่แตกออกมาแล้ว
    zips = sorted((j.pack["size"] for j in jobs if j.pack["kind"] == "zip"), reverse=True)
    need += sum(zips[:workers])  # zip ที่พักไว้พร้อมกัน (ลบหลังแตกไฟล์)
    probe = root
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    if free < need * 1.05 + 50 * 1024 * 1024:
        raise LauncherError(f"พื้นที่ว่างไม่พอ: ต้องการ {fmt_size(need)} เหลือ {fmt_size(free)}")


def finalize(manifest: dict, root: Path, state: InstallState, channel: str) -> None:
    keep = {it["path"] for it in manifest["files"]}
    stale = (set(state.files) - keep) | set(manifest.get("delete", []))
    for rel in stale:
        if rel in keep:
            continue
        try:
            p = safe_join(root, rel)
            if p.is_file():
                p.unlink()
                parent = p.parent
                while parent != root.resolve() and not any(parent.iterdir()):
                    parent.rmdir()
                    parent = parent.parent
        except (LauncherError, OSError) as e:
            log(f"ลบไฟล์เก่าไม่ได้ {rel}: {e}")
    state.files = {}
    for it in manifest["files"]:
        st = safe_join(root, it["path"]).stat()
        state.files[it["path"]] = {"sha256": it["sha256"], "size": st.st_size,
                                   "mtime_ns": st.st_mtime_ns}
    state.version = manifest["game_version"]
    state.channel = channel
    state.save()


# ---------------------------------------------------------------- ดาวน์โหลด
class Progress:
    def __init__(self, total: int):
        self.total = total
        self.done = 0
        self.lock = threading.Lock()
        self.samples = deque([(time.monotonic(), 0)])

    def add(self, n: int) -> None:
        with self.lock:
            self.done = max(0, self.done + n)
            self.samples.append((time.monotonic(), self.done))

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            while len(self.samples) > 1 and self.samples[0][0] < now - 5:
                self.samples.popleft()
            t0, d0 = self.samples[0]
            speed = (self.done - d0) / (now - t0) if now > t0 else 0
            eta = (self.total - self.done) / speed if speed > 1 else None
            return self.done, self.total, speed, eta


def download_file(item: dict, dest: Path, progress: Progress, cancel: threading.Event) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = item["size"]
    if size == 0:
        dest.write_bytes(b"")
        return
    part = dest.with_name(dest.name + ".part")
    last = None
    for attempt in range(4):
        if cancel.is_set():
            raise Cancelled()
        counted = 0
        try:
            have = part.stat().st_size if part.exists() else 0
            if have > size:
                part.unlink()
                have = 0
            counted = have
            progress.add(have)
            if have < size:
                headers = {"Range": f"bytes={have}-"} if have else {}
                with _session.get(item["url"], headers=headers, stream=True, timeout=20) as r:
                    if r.status_code == 416:
                        part.unlink(missing_ok=True)
                        raise OSError("range ไม่ถูกต้อง เริ่มใหม่")
                    r.raise_for_status()
                    mode = "ab"
                    if have and r.status_code != 206:  # เซิร์ฟเวอร์ไม่รองรับ Range -> เริ่มใหม่
                        progress.add(-have)
                        counted = 0
                        mode = "wb"
                    elif not have:
                        mode = "wb"
                    with open(part, mode) as f:
                        for chunk in r.iter_content(CHUNK):
                            if cancel.is_set():
                                raise Cancelled()
                            f.write(chunk)
                            progress.add(len(chunk))
                            counted += len(chunk)
            if part.stat().st_size != size or sha256_file(part) != item["sha256"]:
                part.unlink(missing_ok=True)
                raise OSError("ขนาดหรือ hash ไม่ตรง")
            try:
                os.replace(part, dest)
            except PermissionError:
                raise LauncherError(f"เขียนไฟล์ไม่ได้ (เกมเปิดอยู่หรือไม่?): {item.get('path') or item.get('id')}")
            return
        except Cancelled:
            raise
        except LauncherError:
            raise
        except (requests.RequestException, OSError) as e:
            last = e
            log(f"โหลด {item.get('path') or item.get('id')} ครั้งที่ {attempt + 1} ล้มเหลว: {e}")
            progress.add(-counted)
            time.sleep(1 + attempt)
    raise LauncherError(f"โหลดไม่สำเร็จ: {item.get('path') or item.get('id')} ({last})")


def extract_items(zpath: Path, items: list, root: Path, cancel: threading.Event) -> None:
    """แตกเฉพาะไฟล์ที่ต้องการจาก zip ลง .part -> ตรวจ hash -> เปลี่ยนชื่อ (กัน zip-slip ด้วย safe_join)"""
    try:
        zf = zipfile.ZipFile(zpath)
    except (zipfile.BadZipFile, OSError) as e:
        raise LauncherError(f"แพ็ก zip เสียหาย: {e}")
    with zf:
        for it in items:
            if cancel.is_set():
                raise Cancelled()
            try:
                info = zf.getinfo(it["path"])
            except KeyError:
                raise LauncherError(f"ไม่พบไฟล์ในแพ็ก: {it['path']}")
            dest = safe_join(root, it["path"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            part = dest.with_name(dest.name + ".part")
            h = hashlib.sha256()
            n = 0
            try:
                with zf.open(info) as src, open(part, "wb") as out:
                    for chunk in iter(lambda: src.read(CHUNK), b""):
                        n += len(chunk)
                        if n > it["size"]:  # กัน zip bomb
                            raise LauncherError(f"ไฟล์ใหญ่กว่าที่ระบุ: {it['path']}")
                        out.write(chunk)
                        h.update(chunk)
            except (zipfile.BadZipFile, OSError, RuntimeError) as e:
                part.unlink(missing_ok=True)
                raise LauncherError(f"แตกไฟล์ไม่สำเร็จ {it['path']}: {e}")
            if n != it["size"] or h.hexdigest() != it["sha256"]:
                part.unlink(missing_ok=True)
                raise LauncherError(f"ไฟล์ในแพ็กไม่ตรงกับ manifest: {it['path']}")
            try:
                os.replace(part, dest)
            except PermissionError:
                raise LauncherError(f"เขียนไฟล์ไม่ได้ (เกมเปิดอยู่หรือไม่?): {it['path']}")


def run_job(job: Job, root: Path, progress: Progress, cancel: threading.Event,
            on_stage=None) -> None:
    pack = job.pack
    if pack["kind"] == "file":  # ไฟล์เดี่ยวบน Release -> โหลดลงตำแหน่งจริงเลย
        download_file(pack, safe_join(root, job.items[0]["path"]), progress, cancel)
        return
    tmp = root / TMP_DIR
    tmp.mkdir(parents=True, exist_ok=True)
    zpath = tmp / f"{pack['sha256'][:16]}.zip"
    zpath.unlink(missing_ok=True)
    download_file(pack, zpath, progress, cancel)  # ต่อจากเดิมได้ + ตรวจ hash ทั้งแพ็ก
    if on_stage:
        on_stage(f"กำลังแตกไฟล์จาก {pack['id']}...")
    try:
        extract_items(zpath, job.items, root, cancel)
    finally:
        zpath.unlink(missing_ok=True)


def run_jobs(jobs: list, root: Path, progress: Progress, cancel: threading.Event,
             on_stage=None, workers: int = 3) -> None:
    if not jobs:
        return
    jobs = sorted(jobs, key=lambda j: -j.pack["size"])
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(run_job, j, root, progress, cancel, on_stage) for j in jobs]
        try:
            for f in as_completed(futs):
                f.result()
        except BaseException:
            cancel.set()  # หยุดงานอื่นที่เหลือ
            raise
    try:
        (root / TMP_DIR).rmdir()
    except OSError:
        pass


# ---------------------------------------------------------------- อัปเดตตัว Launcher เอง
def check_launcher_update() -> dict | None:
    try:
        info = fetch_json(config.META_BASE + "launcher.json")
    except NotFound:
        return None
    check_url(info["url"])
    info["_mandatory"] = parse_version(config.LAUNCHER_VERSION) < parse_version(
        info.get("min_supported", "0"))
    if parse_version(info["version"]) > parse_version(config.LAUNCHER_VERSION):
        return info
    return None


def cleanup_old_launcher() -> None:
    if not is_frozen():
        return
    exe = Path(sys.executable)
    for suf in (".old", ".new"):
        try:
            exe.with_name(exe.name + suf).unlink()
        except OSError:
            pass


def _terminate() -> None:  # แยกเป็นฟังก์ชันเพื่อให้ทดสอบได้
    os._exit(0)


def apply_self_update(info: dict, on_progress=None) -> None:
    """โหลดตัวใหม่ -> ตรวจ hash -> เปลี่ยนชื่อตัวเก่าเป็น .old -> วางตัวใหม่ -> รัน
    ถ้าตัวใหม่ crash ภายใน 6 วินาทีจะย้อนกลับไปใช้ตัวเก่า
    ฟังก์ชันนี้ไม่ return เมื่อสำเร็จ (ปิดโปรเซสนี้)"""
    if not is_frozen():
        raise LauncherError("อัปเดตตัวเองได้เฉพาะเมื่อรันจากไฟล์ .exe ที่ build แล้ว")
    exe = Path(sys.executable)
    new = exe.with_name(exe.name + ".new")
    old = exe.with_name(exe.name + ".old")
    want = str(info["sha256"]).lower()
    total = int(info.get("size") or 0)

    h = hashlib.sha256()
    done = 0
    try:
        with _session.get(check_url(info["url"]), stream=True, timeout=20) as r:
            r.raise_for_status()
            total = total or int(r.headers.get("content-length", 0))
            with open(new, "wb") as f:
                for chunk in r.iter_content(CHUNK):
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total)
    except (requests.RequestException, OSError) as e:
        new.unlink(missing_ok=True)
        raise LauncherError(f"โหลด Launcher ใหม่ไม่สำเร็จ: {e}")
    if h.hexdigest() != want:
        new.unlink(missing_ok=True)
        raise LauncherError("Launcher ใหม่ hash ไม่ตรง ยกเลิกการอัปเดต")

    try:
        old.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        os.replace(exe, old)  # Windows ยอมให้ rename ไฟล์ที่กำลังรัน
    except OSError as e:
        new.unlink(missing_ok=True)
        raise LauncherError(f"เปลี่ยนชื่อ Launcher เดิมไม่ได้: {e}")
    try:
        os.replace(new, exe)
        if sys.platform != "win32":
            os.chmod(exe, 0o755)
    except OSError as e:
        os.replace(old, exe)
        raise LauncherError(f"วาง Launcher ใหม่ไม่ได้: {e}")

    args = [a for a in sys.argv[1:] if a != "--updated"] + ["--updated"]
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    try:
        proc = subprocess.Popen([str(exe)] + args, env=env, cwd=str(exe.parent), close_fds=True)
    except OSError as e:
        os.replace(old, exe)
        raise LauncherError(f"เปิด Launcher ใหม่ไม่ได้: {e}")
    try:
        code = proc.wait(timeout=6)
    except subprocess.TimeoutExpired:
        code = 0  # ยังรันอยู่ = ปกติ
    if code != 0:
        log(f"Launcher ใหม่ปิดตัวด้วยรหัส {code} -> ย้อนกลับ")
        try:
            os.replace(old, exe)
        except OSError as e:
            log(f"ย้อนกลับไม่ได้: {e}")
        raise SelfUpdateCrashed(f"Launcher v{info['version']} เปิดไม่ขึ้น กลับไปใช้เวอร์ชันเดิม")
    log(f"อัปเดต Launcher เป็น v{info['version']} สำเร็จ")
    _terminate()


# ---------------------------------------------------------------- เปิดเกม / กันเปิดซ้ำ
_lock_sock = None


def acquire_single_instance(wait: float = 0) -> bool:
    global _lock_sock
    port = 20000 + int(hashlib.sha1(config.APP_NAME.encode()).hexdigest()[:4], 16) % 20000
    deadline = time.monotonic() + wait
    while True:
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", port))
            s.listen(1)
            _lock_sock = s
            return True
        except OSError:
            s.close()
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.3)


def installed_ok(root: Path) -> bool:
    try:
        return safe_join(root, config.GAME_EXE).is_file() and bool(InstallState.load(root).version)
    except LauncherError:
        return False


def record_launcher_path() -> None:
    """จดที่อยู่ Launcher (.exe) ไว้ให้เกมอ่าน — ถ้าผู้เล่นเปิดเกมตรงๆ เกมจะใช้ไฟล์นี้เรียก Launcher ขึ้นมา"""
    if not is_frozen():
        return
    try:
        (data_dir() / "launcher_path.txt").write_text(str(Path(sys.executable).resolve()), "utf-8")
    except OSError as e:
        log(f"จดที่อยู่ Launcher ไม่ได้: {e}")


def make_launch_token() -> str:
    """โทเคนแนบตอนเปิดเกม รูปแบบ  <unix-time>.<hmac-sha256-hex>  (เกมตรวจด้วยความลับเดียวกัน)"""
    ts = str(int(time.time()))
    mac = hmac.new(config.LAUNCH_SECRET.encode("utf-8"), ts.encode("ascii"), hashlib.sha256)
    return f"{ts}.{mac.hexdigest()}"


_game_proc = None  # Popen ของเกมที่ Launcher เปิดเอง (กันกดซ้ำช่วงที่เกมเพิ่งเริ่มและยังไม่โผล่ในรายการโปรเซส)


def _find_game_process(exe: Path) -> bool:
    """Windows: ไล่รายการโปรเซสหาไฟล์เกมตัวนี้ (ชื่อตรง และพาธตรงกับที่ติดตั้ง)"""
    import ctypes
    from ctypes import wintypes

    class ENTRY(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k.Process32FirstW.argtypes = k.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ENTRY)]
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                             ctypes.POINTER(wintypes.DWORD)]
    k.CloseHandle.argtypes = [wintypes.HANDLE]

    invalid = ctypes.c_void_p(-1).value
    snap = k.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if snap in (None, invalid):
        return False
    want = os.path.normcase(os.path.realpath(exe))
    try:
        e = ENTRY()
        e.dwSize = ctypes.sizeof(ENTRY)
        ok = k.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.lower() == exe.name.lower():
                h = k.OpenProcess(0x1000, False, e.th32ProcessID)  # PROCESS_QUERY_LIMITED_INFORMATION
                if not h:
                    return True  # เปิดอ่านพาธไม่ได้ แต่ชื่อตรง ถือว่าเกมรันอยู่
                try:
                    buf = ctypes.create_unicode_buffer(1024)
                    size = wintypes.DWORD(len(buf))
                    if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                        if os.path.normcase(os.path.realpath(buf.value)) == want:
                            return True
                    else:
                        return True
                finally:
                    k.CloseHandle(h)
            ok = k.Process32NextW(snap, ctypes.byref(e))
    finally:
        k.CloseHandle(snap)
    return False


def is_game_running(root: Path) -> bool:
    """เกมที่ติดตั้งในโฟลเดอร์นี้กำลังทำงานอยู่หรือไม่ (เปิดจาก Launcher หรือเปิดเองก็นับ)"""
    if _game_proc is not None and _game_proc.poll() is None:
        return True
    if sys.platform != "win32":
        return False
    try:
        return _find_game_process(safe_join(root, config.GAME_EXE))
    except (OSError, AttributeError, ValueError) as e:
        log(f"เช็กโปรเซสเกมไม่ได้: {e}")
        return False


def launch_game(root: Path) -> None:
    global _game_proc
    exe = safe_join(root, config.GAME_EXE)
    if not exe.is_file():
        raise LauncherError(f"ไม่พบไฟล์เกม: {config.GAME_EXE}")
    if not config.LAUNCH_SECRET:
        raise LauncherError("ยังไม่ได้ตั้ง launch_secret ใน config.json")
    if is_game_running(root):
        raise LauncherError("เกมเปิดอยู่แล้ว")
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008  # DETACHED_PROCESS
    args = [str(exe)] + list(config.GAME_ARGS) + ["-launcherToken", make_launch_token()]
    try:
        _game_proc = subprocess.Popen(args, cwd=str(root), close_fds=True, **kw)
    except OSError as e:
        raise LauncherError(f"เปิดเกมไม่ได้: {e}")
