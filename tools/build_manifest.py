#!/usr/bin/env python3
"""สร้าง manifest ของเกมจาก GitHub Releases ของ repo นี้ (รันโดย workflow อัตโนมัติ)

กติกา Release:
  - ตั้ง tag เป็น  <ช่องทาง>-<เวอร์ชัน>   เช่น  stable-1.2.0 , beta-1.3.0
    (ช่องทางต้องอยู่ใน "channels" ของ config.json — tag อื่น เช่น launcher-1.0.0 จะถูกข้าม)
  - ไฟล์ .zip ที่แนบ  = แพ็กเกม (โครงสร้างในซิป = โครงสร้างในโฟลเดอร์เกม)
  - ไฟล์อื่นที่แนบ    = ไฟล์เดี่ยว วางที่ราก เช่น game.exe
  - ชื่อไฟล์ขึ้นต้นด้วย _ = ไม่นำไปใช้
  - ถ้ามีไฟล์ซ้ำข้ามแพ็ก ไฟล์ที่ชื่อแพ็กเรียงทีหลังจะทับ (ใช้ทำแพตช์ เช่น base.zip < patch.zip)
  - Draft release ไม่ถูกนับ (เตรียมไฟล์ใน Draft ได้ แล้วค่อย Publish)
  - แต่ละช่องทางใช้ Release ที่เผยแพร่ล่าสุด

ผลลัพธ์ใน <out>/ :  manifest-<ช่องทาง>.json  และ build-cache.json
(workflow จะนำไปแนบไว้ใน Release ชื่อ "meta" ซึ่ง Launcher อ่านจากที่นั่น)
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests

API = "https://api.github.com"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1024 * 1024), b""):
            h.update(c)
    return h.hexdigest()


# ------------------------------------------------------------ แหล่งข้อมูล Release
class GitHubSource:
    def __init__(self, repo, token):
        self.repo = repo
        self.s = requests.Session()
        self.s.headers.update({"Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2022-11-28"})
        if token:
            self.s.headers["Authorization"] = f"Bearer {token}"

    def releases(self):
        raw, page = [], 1
        while True:
            r = self.s.get(f"{API}/repos/{self.repo}/releases",
                           params={"per_page": 100, "page": page}, timeout=30)
            r.raise_for_status()
            batch = r.json()
            raw += batch
            if len(batch) < 100:
                break
            page += 1
        out = []
        for rel in raw:
            if rel.get("draft"):
                continue
            assets = [{"id": str(a["id"]), "name": a["name"], "size": a["size"],
                       "url": a["browser_download_url"], "fetch": a["url"]}
                      for a in rel.get("assets", []) if a.get("state", "uploaded") == "uploaded"]
            out.append({"tag": rel["tag_name"], "assets": assets,
                        "published": rel.get("published_at") or rel["created_at"]})
        return out

    def materialize(self, asset, tmpdir):
        dest = Path(tmpdir) / f"{asset['id']}.dl"
        with self.s.get(asset["fetch"], headers={"Accept": "application/octet-stream"},
                        stream=True, timeout=60) as r:  # redirect ไป storage (ตัด Authorization ให้เอง)
            r.raise_for_status()
            with open(dest, "wb") as f:
                for c in r.iter_content(1024 * 1024):
                    f.write(c)
        return dest, True


class LocalSource:
    """โหมดทดสอบ: DIR/<tag>/<ไฟล์...>  (ไม่ต้องใช้ GitHub)"""
    def __init__(self, root, base_url):
        self.root, self.base = Path(root), base_url.rstrip("/") + "/"

    def releases(self):
        out = []
        for td in sorted(p for p in self.root.iterdir() if p.is_dir()):
            if td.name.startswith("_"):  # โฟลเดอร์ขึ้นต้น _ = ถือเป็น draft
                continue
            assets = [{"id": f"{td.name}/{f.name}", "name": f.name, "size": f.stat().st_size,
                       "url": f"{self.base}{td.name}/{f.name}", "fetch": str(f)}
                      for f in sorted(td.iterdir()) if f.is_file()]
            out.append({"tag": td.name, "assets": assets,
                        "published": datetime.fromtimestamp(td.stat().st_mtime,
                                                            timezone.utc).isoformat()})
        return out

    def materialize(self, asset, tmpdir):
        return Path(asset["fetch"]), False


# ------------------------------------------------------------ วิเคราะห์แพ็ก
def bad_name(name: str) -> bool:
    parts = name.split("/")
    return (name.startswith("/") or "\\" in name or ".." in parts or ":" in parts[0]
            or any(p == "" for p in parts))


def analyze(asset, path: Path) -> dict:
    sha = sha256_file(path)
    size = path.stat().st_size
    if not asset["name"].lower().endswith(".zip"):
        return {"kind": "file", "sha256": sha, "size": size,
                "files": [{"path": asset["name"], "sha256": sha, "size": size}]}
    files = []
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        sys.exit(f"{asset['name']} ไม่ใช่ไฟล์ zip ที่ถูกต้อง")
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if name.startswith("__MACOSX/") or any(p.startswith(".") for p in name.split("/")):
                continue
            if bad_name(name):
                sys.exit(f"{asset['name']}: ชื่อไฟล์ไม่ปลอดภัย: {name!r}")
            h, n = hashlib.sha256(), 0
            with zf.open(info) as f:
                for c in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(c)
                    n += len(c)
            files.append({"path": name, "sha256": h.hexdigest(), "size": n})
    return {"kind": "zip", "sha256": sha, "size": size, "files": files}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--out", default="dist")
    ap.add_argument("--delete-file", default="delete.txt")
    ap.add_argument("--prev-cache-url", default=None,
                    help="URL ของ build-cache.json รอบก่อน (ค่าเริ่มต้นคำนวณจาก GITHUB_REPOSITORY)")
    ap.add_argument("--local-releases", default=None, help="โหมดทดสอบ: โฟลเดอร์ DIR/<tag>/ไฟล์")
    ap.add_argument("--asset-base", default=None, help="(โหมดทดสอบ) URL ฐานของโฟลเดอร์ข้างบน")
    a = ap.parse_args()

    channels = json.loads(Path(a.config).read_text("utf-8")).get("channels", ["stable"])

    repo = os.environ.get("GITHUB_REPOSITORY")
    if a.local_releases:
        if not a.asset_base:
            sys.exit("--local-releases ต้องใช้คู่กับ --asset-base")
        source = LocalSource(a.local_releases, a.asset_base)
    else:
        if not repo:
            sys.exit("ต้องรันใน GitHub Actions (GITHUB_REPOSITORY) หรือใช้ --local-releases")
        source = GitHubSource(repo, os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))

    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    # cache จากรอบก่อน (asset id + size เหมือนเดิม = ไม่ต้องโหลดแพ็กมาวิเคราะห์ใหม่)
    cache_url = a.prev_cache_url or (
        f"https://github.com/{repo}/releases/download/meta/build-cache.json" if repo else None)
    cache_in = {}
    if cache_url:
        try:
            r = requests.get(cache_url, timeout=20)
            if r.ok:
                cache_in = r.json()
        except (requests.RequestException, ValueError):
            pass
    cache_out = {}

    delete = []
    if Path(a.delete_file).exists():
        delete = [l.strip() for l in Path(a.delete_file).read_text("utf-8").splitlines()
                  if l.strip() and not l.startswith("#")]

    # เลือก Release ล่าสุดของแต่ละช่องทาง
    latest = {}
    for rel in source.releases():
        channel, sep, version = rel["tag"].partition("-")
        if not sep or not version or channel not in channels:
            print(f"ข้าม tag '{rel['tag']}' (ไม่ใช่ <ช่องทาง>-<เวอร์ชัน> ของช่องทาง {channels})")
            continue
        if channel not in latest or rel["published"] > latest[channel][0]["published"]:
            latest[channel] = (rel, version.lstrip("v"))
    if not latest:
        print("ยังไม่มี Release เกมที่เผยแพร่แล้ว (เช่น tag stable-1.0.0) — ไม่มีอะไรให้สร้าง")
        return

    tmp = tempfile.mkdtemp()
    for channel, (rel, version) in sorted(latest.items()):
        packs, files = [], {}
        assets = sorted((x for x in rel["assets"] if not x["name"].startswith("_")),
                        key=lambda x: x["name"])
        for ast in assets:
            c = cache_in.get(ast["id"])
            if c and c.get("size_asset") == ast["size"]:
                info = c
                print(f"  cache: {ast['name']}")
            else:
                if ast["size"] == 0:
                    print(f"  ข้ามไฟล์ว่าง: {ast['name']}")
                    continue
                print(f"  วิเคราะห์: {ast['name']} ({ast['size'] / 1048576:.1f} MB)")
                path, is_tmp = source.materialize(ast, tmp)
                info = analyze(ast, path)
                info["size_asset"] = ast["size"]
                if is_tmp:
                    path.unlink()
            cache_out[ast["id"]] = info
            packs.append({"id": ast["name"], "kind": info["kind"], "url": ast["url"],
                          "sha256": info["sha256"], "size": info["size"]})
            for f in info["files"]:
                if f["path"] in files:
                    print(f"  ทับไฟล์: {f['path']}  ({files[f['path']]['pack']} -> {ast['name']})")
                files[f["path"]] = {**f, "pack": ast["name"]}
        if not files:
            print(f"[{channel}] Release {rel['tag']} ไม่มีไฟล์ที่ใช้ได้ ข้ามช่องทางนี้")
            continue
        used = {f["pack"] for f in files.values()}
        packs = [p for p in packs if p["id"] in used]  # ตัดแพ็กที่ถูกทับจนไม่เหลือไฟล์
        manifest = {"game_version": version, "channel": channel, "tag": rel["tag"],
                    "packs": packs, "files": sorted(files.values(), key=lambda f: f["path"]),
                    "delete": delete}
        (out / f"manifest-{channel}.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
        print(f"[{channel}] v{version}: {len(packs)} แพ็ก, {len(files)} ไฟล์, "
              f"{sum(f['size'] for f in files.values()) / 1048576:.1f} MB")
    shutil.rmtree(tmp, ignore_errors=True)

    (out / "build-cache.json").write_text(json.dumps(cache_out), "utf-8")
    print(f"เสร็จแล้ว -> {out}/")


if __name__ == "__main__":
    main()
