"""หน้าต่าง Launcher (Tkinter) — รันด้วย:  python app.py"""
from __future__ import annotations

import json
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import config
import core

BG, PANEL, FG, MUTED = "#0e1116", "#161b22", "#e6edf3", "#8b949e"
ACCENT, ERR = "#f5c542", "#ff6b6b"
W, BANNER_H = 900, 300


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f"{config.APP_NAME} Launcher  v{config.LAUNCHER_VERSION}")
        self.root.geometry(f"{W}x560")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)

        self.settings = core.Settings.load()
        self.q: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.mode = "start"       # start | retry | download
        self.mode_label = ""      # ข้อความบนปุ่มโหลด/อัปเดต
        self.last_flow = (False, False)  # (repair, download) ของรอบล่าสุด ไว้ใช้ตอนกด "ลองใหม่"
        self.news: list = []
        self.news_idx = 0

        self._build()
        core.cleanup_old_launcher()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.poll)
        self.root.after(6000, self.rotate_news)
        self.start_flow()

    # ------------------------------------------------------------ UI
    def _build(self):
        self.canvas = tk.Canvas(self.root, width=W, height=BANNER_H, bg=PANEL,
                                highlightthickness=0, cursor="hand2")
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self.on_banner_click)
        self.draw_banner()

        bottom = tk.Frame(self.root, bg=BG)
        bottom.pack(fill="both", expand=True, padx=24, pady=16)

        left = tk.Frame(bottom, bg=BG)
        left.pack(side="left", fill="both", expand=True)
        self.status_var = tk.StringVar(value="กำลังเริ่มต้น...")
        self.detail_var = tk.StringVar(value="")
        self.status_lbl = tk.Label(left, textvariable=self.status_var, bg=BG, fg=FG,
                                   font=("Segoe UI", 12, "bold"), anchor="w")
        self.status_lbl.pack(fill="x")
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("A.Horizontal.TProgressbar", troughcolor=PANEL, background=ACCENT,
                        bordercolor=PANEL, lightcolor=ACCENT, darkcolor=ACCENT)
        self.pbar = ttk.Progressbar(left, style="A.Horizontal.TProgressbar", maximum=100)
        self.pbar.pack(fill="x", pady=(10, 6))
        tk.Label(left, textvariable=self.detail_var, bg=BG, fg=MUTED, font=("Segoe UI", 9),
                 anchor="w").pack(fill="x")

        small = tk.Frame(left, bg=BG)
        small.pack(anchor="w", pady=(14, 0))
        for text, cmd in (("ตั้งค่า", self.open_settings), ("ตรวจสอบ/ซ่อมแซมไฟล์", self.repair)):
            tk.Button(small, text=text, command=cmd, bg=PANEL, fg=FG, bd=0, padx=12, pady=4,
                      activebackground="#222a35", activeforeground=FG,
                      font=("Segoe UI", 9)).pack(side="left", padx=(0, 8))

        self.action_btn = tk.Button(bottom, text="เริ่มเกม", command=self.on_action, width=14,
                                    font=("Segoe UI", 16, "bold"), bd=0, state="disabled",
                                    bg=ACCENT, fg="#111", disabledforeground="#555",
                                    activebackground="#ffd966")
        self.action_btn.pack(side="right", padx=(20, 0), ipady=10)

    def draw_banner(self):
        c = self.canvas
        c.delete("all")
        item = self.news[self.news_idx] if self.news else None
        if item and item.get("photo"):
            c.create_image(0, 0, image=item["photo"], anchor="nw")
        if item:
            c.create_text(25, BANNER_H - 38, text=item["title"], anchor="w", fill="#000",
                          font=("Segoe UI", 18, "bold"))
            c.create_text(24, BANNER_H - 40, text=item["title"], anchor="w", fill="#fff",
                          font=("Segoe UI", 18, "bold"))
            for i in range(len(self.news)):
                x = W - 25 - (len(self.news) - 1 - i) * 16
                c.create_oval(x - 4, BANNER_H - 24, x + 4, BANNER_H - 16, outline="",
                              fill=ACCENT if i == self.news_idx else "#555")
        else:
            c.create_text(W // 2, BANNER_H // 2, text=config.APP_NAME, fill=MUTED,
                          font=("Segoe UI", 32, "bold"))

    def rotate_news(self):
        if len(self.news) > 1:
            self.news_idx = (self.news_idx + 1) % len(self.news)
            self.draw_banner()
        self.root.after(6000, self.rotate_news)

    def on_banner_click(self, _e):
        if self.news:
            link = self.news[self.news_idx].get("link", "")
            if link.startswith(("https://", "http://")):
                webbrowser.open(link)

    # ------------------------------------------------------------ รับข้อความจาก worker
    def post(self, kind, arg=None):
        self.q.put((kind, arg))

    def poll(self):
        try:
            while True:
                kind, arg = self.q.get_nowait()
                getattr(self, "ev_" + kind)(arg)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def set_action(self, mode, enabled):
        self.mode = mode
        text = {"start": "เริ่มเกม", "retry": "ลองใหม่",
                "download": self.mode_label or "โหลดเกม"}[mode]
        self.action_btn.config(text=text, state="normal" if enabled else "disabled")

    def ev_status(self, text):
        self.status_var.set(text)
        self.status_lbl.config(fg=FG)

    def ev_progress(self, arg):
        frac, detail = arg
        self.pbar["value"] = max(0, min(1, frac)) * 100
        self.detail_var.set(detail)

    def ev_news(self, items):
        self.news = []
        for it in items:
            photo = None
            if it.get("image_path"):
                try:
                    photo = tk.PhotoImage(file=it["image_path"])
                except tk.TclError:
                    photo = None
            self.news.append({**it, "photo": photo})
        self.news_idx = 0
        self.draw_banner()

    def ev_busy(self, flag):
        self.busy = flag
        if flag:
            self.set_action(self.mode, False)

    def ev_ready(self, arg):
        version, offline = arg
        self.busy = False
        self.pbar["value"] = 100
        self.status_var.set(f"พร้อมเริ่มเกม v{version}" + (" (ออฟไลน์)" if offline else ""))
        self.status_lbl.config(fg=FG)
        self.detail_var.set("")
        self.set_action("start", True)

    def ev_need(self, arg):
        """ต้องโหลด/อัปเดตไฟล์เกม — รอให้ผู้เล่นกดเอง ยังไม่โหลดอะไรทั้งนั้น"""
        version, n_files, size, installed = arg
        self.busy = False
        self.pbar["value"] = 0
        self.status_lbl.config(fg=ACCENT)
        if installed:
            self.status_var.set(f"มีอัปเดตใหม่ v{version}")
            self.mode_label = "อัปเดตเกม"
        else:
            self.status_var.set(f"ยังไม่ได้ติดตั้งเกม (v{version})")
            self.mode_label = "โหลดเกม"
        self.detail_var.set(f"{n_files} ไฟล์ • ขนาดดาวน์โหลดประมาณ {core.fmt_size(size)} "
                            f"— กดปุ่มเพื่อเริ่มโหลด")
        self.set_action("download", True)

    def ev_maint(self, msg):
        self.busy = False
        self.status_var.set("ปิดปรับปรุงชั่วคราว")
        self.status_lbl.config(fg=ACCENT)
        self.detail_var.set(msg)
        self.pbar["value"] = 0
        self.set_action("retry", True)

    def ev_error(self, msg):
        self.busy = False
        self.status_var.set(msg)
        self.status_lbl.config(fg=ERR)
        self.detail_var.set("กด 'ลองใหม่' หรือดูรายละเอียดในไฟล์ launcher.log")
        self.set_action("retry", True)

    # ------------------------------------------------------------ ปุ่มต่างๆ
    def on_action(self):
        if self.mode == "retry":
            self.start_flow(*self.last_flow)
            return
        if self.mode == "download":
            self.start_flow(download=True)
            return
        try:
            core.launch_game(Path(self.settings.install_dir))
        except core.LauncherError as e:
            messagebox.showerror(config.APP_NAME, str(e))
            return
        if self.settings.close_on_launch:
            self.root.after(800, self.root.destroy)

    def repair(self):
        if self.busy:
            return
        if messagebox.askyesno(config.APP_NAME, "ตรวจสอบไฟล์ทั้งหมดและโหลดใหม่เฉพาะไฟล์ที่เสีย?\n"
                                                "อาจใช้เวลาสักครู่"):
            self.start_flow(repair=True, download=True)

    def open_settings(self):
        if self.busy:
            return
        win = tk.Toplevel(self.root)
        win.title("ตั้งค่า")
        win.configure(bg=BG, padx=20, pady=16)
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        dir_var = tk.StringVar(value=self.settings.install_dir)
        ch_var = tk.StringVar(value=self.settings.channel)
        close_var = tk.BooleanVar(value=self.settings.close_on_launch)

        tk.Label(win, text="โฟลเดอร์ติดตั้ง", bg=BG, fg=FG).grid(row=0, column=0, sticky="w")
        tk.Entry(win, textvariable=dir_var, width=48).grid(row=1, column=0, pady=4)
        tk.Button(win, text="เลือก...", command=lambda: dir_var.set(
            filedialog.askdirectory(initialdir=dir_var.get()) or dir_var.get())
        ).grid(row=1, column=1, padx=6)
        tk.Label(win, text="ช่องทางอัปเดต", bg=BG, fg=FG).grid(row=2, column=0, sticky="w",
                                                              pady=(10, 0))
        ttk.Combobox(win, textvariable=ch_var, values=config.CHANNELS,
                     state="readonly").grid(row=3, column=0, sticky="w", pady=4)
        tk.Checkbutton(win, text="ปิด Launcher หลังเริ่มเกม", variable=close_var, bg=BG,
                       fg=FG, selectcolor=PANEL, activebackground=BG,
                       activeforeground=FG).grid(row=4, column=0, sticky="w", pady=(10, 0))

        def save():
            changed = (dir_var.get() != self.settings.install_dir
                       or ch_var.get() != self.settings.channel)
            self.settings.install_dir = dir_var.get().strip()
            self.settings.channel = ch_var.get()
            self.settings.close_on_launch = close_var.get()
            self.settings.save()
            win.destroy()
            if changed:
                self.start_flow()

        tk.Button(win, text="บันทึก", command=save, bg=ACCENT, fg="#111", bd=0, padx=18,
                  pady=4).grid(row=5, column=0, sticky="e", pady=(16, 0))

    def on_close(self):
        if self.busy and not messagebox.askyesno(config.APP_NAME, "กำลังอัปเดตอยู่ ต้องการยกเลิกและปิดหรือไม่?"):
            return
        self.cancel.set()
        self.root.destroy()

    # ------------------------------------------------------------ ขั้นตอนอัปเดต (รันใน thread)
    def start_flow(self, repair=False, download=False):
        """download=False: เช็กอย่างเดียว (ตอนเปิดโปรแกรม) ไม่โหลดไฟล์เกม
        download=True : ผู้เล่นกดโหลด/อัปเดต/ซ่อมแซมเอง ถึงจะโหลดจริง"""
        if self.busy:
            return
        self.last_flow = (repair, download)
        self.busy = True
        self.cancel = threading.Event()
        self.pbar["value"] = 0
        self.detail_var.set("")
        self.set_action(self.mode, False)
        threading.Thread(target=self.flow, args=(repair, download, self.cancel),
                         daemon=True).start()

    def load_news(self):
        try:
            data = core.fetch_news()
            (core.data_dir() / "news_cache.json").write_text(
                json.dumps(data, ensure_ascii=False), "utf-8")
        except Exception as e:  # noqa: BLE001
            core.log(f"โหลดข่าวไม่ได้: {e}")
            try:
                data = json.loads((core.data_dir() / "news_cache.json").read_text("utf-8"))
            except (OSError, ValueError):
                return
        items = []
        for it in data.get("items", [])[:8]:
            path = None
            try:
                path = core.fetch_banner(it)
            except Exception as e:  # noqa: BLE001
                core.log(f"โหลดแบนเนอร์ไม่ได้: {e}")
            items.append({"title": str(it.get("title", "")), "link": str(it.get("link", "")),
                          "image_path": str(path) if path else None})
        self.post("news", items)

    def flow(self, repair: bool, download: bool, cancel: threading.Event):
        s = self.settings
        root = Path(s.install_dir)
        try:
            if not config.REPO:
                raise core.LauncherError(
                    "ยังไม่ได้ระบุ repo (รันจากซอร์สให้ตั้ง MYGAME_REPO=user/repo — "
                    "ถ้าเป็น .exe ที่ build จาก GitHub Actions จะใส่ให้เอง)")
            # 1) อัปเดตตัว Launcher เอง (เฉพาะตอนเป็น .exe)
            info = None
            if core.is_frozen():
                self.post("status", "กำลังตรวจสอบ Launcher...")
                try:
                    info = core.check_launcher_update()
                except (core.LauncherError, KeyError, ValueError) as e:
                    core.log(f"เช็ก Launcher ไม่ได้: {e}")
            if info and info["version"] != s.bad_launcher_version:
                if core.is_frozen():
                    self.post("status", f"กำลังอัปเดต Launcher เป็น v{info['version']}...")
                    try:
                        core.apply_self_update(info, lambda d, t: self.post(
                            "progress", (d / t if t else 0,
                                         f"{core.fmt_size(d)} / {core.fmt_size(t)}")))
                    except core.SelfUpdateCrashed as e:
                        s.bad_launcher_version = info["version"]
                        s.save()
                        core.log(str(e))
                    except core.LauncherError as e:
                        core.log(str(e))
                        if info["_mandatory"]:
                            raise
                elif info["_mandatory"]:
                    raise core.LauncherError("Launcher เวอร์ชันนี้เก่าเกินไป ต้องอัปเดต")
            elif info and info["_mandatory"]:
                raise core.LauncherError("Launcher เวอร์ชันนี้เก่าเกินไป ต้องอัปเดต")

            # 2) สถานะปิดปรับปรุง
            offline = False
            try:
                st = core.fetch_status()
                if st.get("maintenance"):
                    self.post("maint", str(st.get("message") or "กรุณารอสักครู่"))
                    return
            except (core.LauncherError, ValueError) as e:
                core.log(f"ออฟไลน์/เช็กสถานะไม่ได้: {e}")
                offline = True

            # 3) ข่าว (ไม่บล็อกการอัปเดต)
            threading.Thread(target=self.load_news, daemon=True).start()

            # 4) ไฟล์เกม
            state = core.InstallState.load(root)
            if offline:
                if core.installed_ok(root):
                    self.post("ready", (state.version, True))
                else:
                    self.post("error", "ออฟไลน์ และยังไม่ได้ติดตั้งเกม")
                return

            self.post("status", "กำลังตรวจสอบไฟล์เกม...")
            manifest = core.fetch_manifest(s.channel)
            todo = core.plan_update(
                manifest, root, state, full_verify=repair,
                on_scan=lambda i, n: self.post("progress", (i / n if n else 1,
                                                            f"ตรวจไฟล์ {i}/{n}")))
            if todo and not download:
                # ยังไม่โหลดอะไร — แจ้งขนาดแล้วรอผู้เล่นกดปุ่มเอง
                jobs = core.build_jobs(manifest, todo)
                size = sum(j.pack["size"] for j in jobs)
                self.post("need", (manifest["game_version"], len(todo), size,
                                   core.installed_ok(root)))
                return
            if todo:
                jobs = core.build_jobs(manifest, todo)
                core.check_disk_space(root, jobs)
                prog = core.Progress(sum(j.pack["size"] for j in jobs))
                stop = threading.Event()

                def ticker():
                    while not stop.is_set():
                        d, t, sp, eta = prog.snapshot()
                        self.post("progress", (d / t if t else 1,
                                               f"{core.fmt_size(d)} / {core.fmt_size(t)}   "
                                               f"{core.fmt_size(sp)}/s   เหลือ {core.fmt_eta(eta)}"))
                        stop.wait(0.2)

                threading.Thread(target=ticker, daemon=True).start()
                self.post("status", f"กำลังดาวน์โหลด {len(jobs)} แพ็ก ({len(todo)} ไฟล์)...")
                try:
                    core.run_jobs(jobs, root, prog, cancel,
                                  on_stage=lambda t: self.post("status", t))
                finally:
                    stop.set()
            core.finalize(manifest, root, state, s.channel)
            self.post("ready", (manifest["game_version"], False))
        except core.Cancelled:
            self.post("error", "ยกเลิกการอัปเดต")
        except core.LauncherError as e:
            core.log(f"ผิดพลาด: {e}")
            self.post("error", str(e))
        except Exception as e:  # noqa: BLE001
            core.log(f"ข้อผิดพลาดไม่คาดคิด: {e!r}")
            self.post("error", f"เกิดข้อผิดพลาด: {e}")

    def run(self):
        self.root.mainloop()


def main():
    if not core.acquire_single_instance(10 if "--updated" in sys.argv else 0):
        r = tk.Tk()
        r.withdraw()
        messagebox.showinfo(config.APP_NAME, "Launcher เปิดอยู่แล้ว")
        return
    App().run()


if __name__ == "__main__":
    main()
