"""หน้าต่าง Launcher (Tkinter) — รันด้วย:  python app.py

หน้าตา: หน้าต่างไร้กรอบขนาด 1280x720 พื้นหลังเต็มจอ ชื่อเกมใหญ่ด้านซ้าย
การ์ดข่าวกระจกฝ้ามุมซ้ายล่าง ปุ่มเหลืองทรงเม็ดยาที่มุมขวาล่าง
ไอคอนตั้งค่า/ย่อ/ปิดมุมขวาบน — ทั้งหมดวาดบน Canvas ใบเดียว
"""
from __future__ import annotations

import ctypes
import json
import queue
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

import art
import config
import core

W, H = art.W, art.H
FONT = "Segoe UI"
FG, MUTED, SOFT = "#ffffff", "#c9d6de", "#9fb4c0"
ACCENT, ACCENT_HOVER, ERR = "#ffd900", "#ffe64d", "#ff8a8a"
SHADOW = "#143244"
PANEL, BG = "#161b22", "#0e1116"   # ใช้กับหน้าต่างตั้งค่า
BTN = (935, 607, 1223, 663)        # ปุ่มหลัก (x1, y1, x2, y2)
NEWS_ROWS = 3
ROW_Y0, ROW_DY = 598, 24


def pill(c: tk.Canvas, box, fill, tag):
    """วาดทรงเม็ดยา (สี่เหลี่ยมมุมมนสุด) ด้วยวงกลม 2 ข้าง + แถบกลาง"""
    x1, y1, x2, y2 = box
    r = (y2 - y1) / 2
    c.create_oval(x1, y1, x1 + 2 * r, y2, fill=fill, outline="", tags=tag)
    c.create_oval(x2 - 2 * r, y1, x2, y2, fill=fill, outline="", tags=tag)
    c.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline="", tags=tag)


def set_app_id():
    """Windows: แยกกลุ่มแถบงานของเราออกจาก python.exe ไอคอนจะได้เป็นของ Launcher"""
    if sys.platform == "win32":
        try:
            name = "".join(ch for ch in config.APP_NAME if ch.isalnum()) or "Game"
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{name}.Launcher")
        except (AttributeError, OSError):
            pass


class App:
    def __init__(self):
        set_app_id()
        self.root = tk.Tk()
        self.root.title(f"{config.APP_NAME} Launcher  v{config.LAUNCHER_VERSION}")
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{W}x{H}+{max(0, (sw - W) // 2)}+{max(0, (sh - H) // 2 - 20)}")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)
        self.root.overrideredirect(True)          # ไร้กรอบ ลากย้ายเองได้
        try:                                      # ไอคอนหน้าต่าง/แถบงาน (วาดจาก art.make_icon)
            self.icons = [ImageTk.PhotoImage(art.make_icon(n)) for n in (16, 32, 48, 256)]
            self.root.iconphoto(True, *self.icons)
        except (tk.TclError, OSError):
            pass
        if sys.platform != "win32":
            self.root.bind("<Map>", self.on_map)  # กลับมาไร้กรอบหลังย่อหน้าต่าง

        self.settings = core.Settings.load()
        self.q: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.mode = "start"       # start | retry | download
        self.mode_label = ""      # ข้อความบนปุ่มโหลด/อัปเดต
        self.btn_enabled = False
        self.game_running = False
        self.ready_text = ""      # ข้อความ "พร้อมเริ่มเกม" ไว้คืนค่าหลังเกมปิด
        self.last_flow = (False, False)  # (repair, download) ของรอบล่าสุด ไว้ใช้ตอนกด "ลองใหม่"
        self.news: list = []
        self.news_idx = 0
        self.status, self.status_color = "กำลังเริ่มต้น...", FG
        self.detail = ""
        self.progress: float | None = None
        self.hits: list = []      # (x1, y1, x2, y2, ชื่อ, ฟังก์ชัน)
        self.hover = ""
        self._drag = None

        self.f_row = tkfont.Font(family=FONT, size=-15)
        self.canvas = tk.Canvas(self.root, width=W, height=H, bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack()
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<Motion>", self.on_motion)
        self.canvas.bind("<Leave>", lambda _e: self.set_hover(""))

        self.bg_photo = None
        self.bg_item = self.canvas.create_image(0, 0, anchor="nw")
        self.set_background(self.load_cached_bg())
        self.draw_title()
        self.draw_dynamic()
        self.draw_status()

        self.root.after(50, self.show_in_taskbar)
        core.cleanup_old_launcher()
        core.record_launcher_path()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.poll)
        self.root.after(6000, self.rotate_news)
        self.root.after(1500, self.tick_game)
        self.start_flow()

    # ------------------------------------------------------------ พื้นหลัง
    def load_cached_bg(self):
        try:
            with Image.open(core.data_dir() / "background.png") as im:
                return im.convert("RGB")
        except (OSError, ValueError):
            return None

    def set_background(self, img):
        self.bg_photo = ImageTk.PhotoImage(art.compose_background(img))
        self.canvas.itemconfig(self.bg_item, image=self.bg_photo)

    # ------------------------------------------------------------ วาด
    def text(self, x, y, s, size, fill=FG, bold=False, anchor="w", shadow=True, tags=""):
        font = (FONT, -size, "bold") if bold else (FONT, -size)
        if shadow:
            self.canvas.create_text(x + 1, y + 2, text=s, anchor=anchor, fill=SHADOW, font=font,
                                    tags=tags)
        self.canvas.create_text(x, y, text=s, anchor=anchor, fill=fill, font=font, tags=tags)

    def draw_title(self):
        self.text(60, 250, config.APP_NAME.upper(), 66, bold=True, tags="title")
        sub = getattr(config, "SUBTITLE", "")  # ไม่ต้องแก้ config.py
        if sub:
            self.text(62, 303, sub, 25, bold=True, tags="title")

    def truncate(self, s: str, maxw: int) -> str:
        if self.f_row.measure(s) <= maxw:
            return s
        while s and self.f_row.measure(s + "…") > maxw:
            s = s[:-1]
        return s + "…"

    def draw_dynamic(self):
        """ไอคอนมุมขวาบน + การ์ดข่าว + ปุ่มหลัก (วาดใหม่ทุกครั้งที่สถานะ/โฮเวอร์เปลี่ยน)"""
        c = self.canvas
        c.delete("dyn")
        self.hits = []
        hv = self.hover

        # ไอคอนมุมขวาบน: ตั้งค่า / ย่อ / ปิด
        def icon_color(name):
            return FG if hv == name else MUTED
        col = icon_color("gear")
        c.create_oval(1146, 23, 1164, 41, outline=col, width=2, tags="dyn")
        c.create_oval(1151, 28, 1159, 36, outline=col, width=2, tags="dyn")
        self.hits.append((1136, 14, 1174, 50, "gear", self.open_settings))
        col = icon_color("min")
        c.create_line(1190, 32, 1206, 32, fill=col, width=2, tags="dyn")
        self.hits.append((1180, 14, 1216, 50, "min", self.minimize))
        col = icon_color("close")
        c.create_line(1235, 24, 1251, 40, fill=col, width=2, tags="dyn")
        c.create_line(1251, 24, 1235, 40, fill=col, width=2, tags="dyn")
        self.hits.append((1222, 14, 1262, 50, "close", self.on_close))

        # การ์ดข่าว
        x1, y1, x2, y2 = art.CARD
        item = self.news[self.news_idx] if self.news else None
        if item and item.get("thumb"):
            c.create_image(x1, y1, image=item["thumb"], anchor="nw", tags="dyn")
        else:
            c.create_text((x1 + x2) // 2, y1 + art.THUMB_SIZE[1] // 2,
                          text=(item["title"] if item else config.APP_NAME), fill=SOFT,
                          font=(FONT, -22, "bold"), width=x2 - x1 - 40, tags="dyn")
        if item:
            self.hits.append((x1, y1, x2, y1 + art.THUMB_SIZE[1], "thumb",
                              lambda i=self.news_idx: self.open_link(i)))
        c.create_text(x1 + 8, 567, text="News", anchor="w", fill=FG, font=(FONT, -17, "bold"),
                      tags="dyn")
        c.create_line(x1 + 8, 583, x1 + 36, 583, fill=ACCENT, width=2, tags="dyn")
        page = (self.news_idx // NEWS_ROWS) * NEWS_ROWS
        for n, i in enumerate(range(page, min(page + NEWS_ROWS, len(self.news)))):
            it = self.news[i]
            y = ROW_Y0 + n * ROW_DY
            name = f"row{i}"
            col = FG if (hv == name or i == self.news_idx) else MUTED
            date = it.get("date", "")
            maxw = (x2 - x1) - 16 - 20 - (self.f_row.measure(date) + 14 if date else 0)
            c.create_text(x1 + 10, y, text=self.truncate(it["title"], maxw), anchor="w",
                          fill=col, font=(FONT, -15), tags="dyn")
            if date:
                c.create_text(x2 - 16, y, text=date, anchor="e", fill=SOFT, font=(FONT, -14),
                              tags="dyn")
            self.hits.append((x1 + 4, y - 11, x2 - 4, y + 11, name,
                              lambda i=i: self.select_news(i)))

        # ปุ่มหลัก
        label = {"start": "กำลังเล่นอยู่" if self.game_running else "เริ่มเกม",
                 "retry": "ลองใหม่", "download": self.mode_label or "โหลดเกม"}[self.mode]
        bx1, by1, bx2, by2 = BTN
        if self.btn_enabled:
            pill(c, (bx1, by1 + 4, bx2, by2 + 4), "#8a6f00", "dyn")
            pill(c, BTN, ACCENT_HOVER if hv == "btn" else ACCENT, "dyn")
            tcol = "#111111"
        else:
            pill(c, BTN, "#b9b08a", "dyn")
            tcol = "#6b6650"
        c.create_text((bx1 + bx2) // 2, (by1 + by2) // 2, text=label, fill=tcol,
                      font=(FONT, -22, "bold"), tags="dyn")
        self.hits.append((bx1, by1, bx2, by2, "btn", self.on_action))

        # ลิงก์เล็กใต้ปุ่ม + เลขเวอร์ชัน
        col = FG if hv == "repair" else SOFT
        c.create_text((bx1 + bx2) // 2, 690, text="ตรวจสอบ/ซ่อมแซมไฟล์", fill=col,
                      font=(FONT, -13, "underline" if hv == "repair" else "normal"), tags="dyn")
        self.hits.append((bx1 + 60, 678, bx2 - 60, 702, "repair", self.repair))
        c.create_text(W - 18, H - 12, text=f"Launcher v{config.LAUNCHER_VERSION}", anchor="e",
                      fill=SOFT, font=(FONT, -11), tags="dyn")

    def draw_status(self):
        c = self.canvas
        c.delete("stat")
        x = 485
        self.text(x, 622, self.status, 20, fill=self.status_color, bold=True, tags="stat")
        if self.progress is not None:
            w = 410
            c.create_line(x, 648, x + w, 648, fill="#27495a", width=6, capstyle="round",
                          tags="stat")
            frac = max(0.0, min(1.0, self.progress))
            if frac > 0:
                c.create_line(x, 648, x + max(1, w * frac), 648, fill=ACCENT, width=6,
                              capstyle="round", tags="stat")
        if self.detail:
            self.text(x, 667, self.detail, 13, fill=MUTED, tags="stat")

    # ------------------------------------------------------------ เมาส์ / หน้าต่าง
    def hit(self, x, y):
        for x1, y1, x2, y2, name, cb in reversed(self.hits):
            if x1 <= x <= x2 and y1 <= y <= y2:
                return name, cb
        return None

    def set_hover(self, name):
        if name != self.hover:
            self.hover = name
            self.draw_dynamic()

    def on_motion(self, e):
        h = self.hit(e.x, e.y)
        name = h[0] if h else ""
        if name == "btn" and not self.btn_enabled:
            name = ""
        self.canvas.config(cursor="hand2" if name else "")
        self.set_hover(name)

    def on_press(self, e):
        h = self.hit(e.x, e.y)
        if h:
            self._drag = None
            h[1]()
        else:  # กดที่ว่าง = ลากย้ายหน้าต่าง
            self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def on_drag(self, e):
        if self._drag:
            self.root.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def hwnd(self):
        return ctypes.windll.user32.GetParent(self.root.winfo_id())

    def show_in_taskbar(self):
        """หน้าต่างไร้กรอบ (overrideredirect) ปกติไม่ขึ้นแถบงาน — บังคับให้เป็น App window"""
        if sys.platform != "win32":
            return
        try:
            u = ctypes.windll.user32
            GWL_EXSTYLE, WS_EX_APPWINDOW, WS_EX_TOOLWINDOW = -20, 0x00040000, 0x00000080
            h = self.hwnd()
            style = u.GetWindowLongW(h, GWL_EXSTYLE)
            u.SetWindowLongW(h, GWL_EXSTYLE, (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW)
            self.root.withdraw()                  # ต้องซ่อน-แสดงใหม่ Windows ถึงอัปเดตแถบงาน
            self.root.after(20, self.root.deiconify)
        except (AttributeError, OSError) as e:
            core.log(f"ตั้งค่าไอคอนแถบงานไม่ได้: {e}")

    def minimize(self):
        if sys.platform == "win32":
            try:
                ctypes.windll.user32.ShowWindow(self.hwnd(), 6)  # SW_MINIMIZE
                return
            except (AttributeError, OSError):
                pass
        self.root.overrideredirect(False)   # ระบบอื่น: คืนกรอบชั่วคราวแล้วย่อ
        self.root.iconify()

    def on_map(self, e):
        if e.widget is self.root and self.root.state() == "normal":
            self.root.overrideredirect(True)

    # ------------------------------------------------------------ ข่าว
    def select_news(self, i):
        self.news_idx = i
        self.draw_dynamic()
        self.open_link(i)

    def open_link(self, i):
        if 0 <= i < len(self.news):
            link = self.news[i].get("link", "")
            if link.startswith(("https://", "http://")):
                webbrowser.open(link)

    def rotate_news(self):
        if len(self.news) > 1:
            self.news_idx = (self.news_idx + 1) % len(self.news)
            self.draw_dynamic()
        self.root.after(6000, self.rotate_news)

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
        # เกมกำลังรัน: ห้ามกดเริ่มซ้ำ และห้ามโหลด/อัปเดต (ไฟล์เกมถูกล็อกอยู่)
        self.btn_enabled = enabled and not (self.game_running and mode in ("start", "download"))
        self.draw_dynamic()

    def refresh_running_ui(self):
        """ปรับสถานะตามว่าเกมรันอยู่หรือไม่ (เรียกหลัง set_action ทุกครั้งที่เป็นโหมด start)"""
        if self.busy or self.mode != "start":
            return
        if self.game_running:
            self.set_status("เกมกำลังทำงานอยู่", ACCENT, "ปิดเกมก่อนถึงจะเริ่มใหม่หรืออัปเดตได้")
        else:
            self.set_status(self.ready_text, FG, "")

    def tick_game(self):
        """เช็กทุก 1.5 วินาทีว่าเกมยังรันอยู่ไหม — เกมปิดเมื่อไหร่ปุ่มกลับมากดได้เอง"""
        try:
            running = core.is_game_running(Path(self.settings.install_dir))
        except Exception as e:  # noqa: BLE001
            core.log(f"tick_game: {e}")
            running = self.game_running
        if running != self.game_running:
            self.game_running = running
            if not self.busy and self.mode in ("start", "download"):
                self.set_action(self.mode, True)
                self.refresh_running_ui()
        self.root.after(1500, self.tick_game)

    def set_status(self, text, color=FG, detail=None):
        self.status, self.status_color = text, color
        if detail is not None:
            self.detail = detail
        self.draw_status()

    def ev_status(self, text):
        self.set_status(text)

    def ev_progress(self, arg):
        frac, detail = arg
        self.progress = frac
        self.detail = detail
        self.draw_status()

    def ev_bg(self, path):
        try:
            with Image.open(path) as im:
                self.set_background(im.convert("RGB"))
        except (OSError, ValueError) as e:
            core.log(f"ใช้พื้นหลังไม่ได้: {e}")
        self.draw_dynamic()

    def ev_news(self, items):
        self.news = []
        for it in items:
            thumb = None
            if it.get("image_path"):
                try:
                    thumb = ImageTk.PhotoImage(art.make_thumb(it["image_path"]))
                except (OSError, ValueError):
                    thumb = None
            self.news.append({**it, "thumb": thumb})
        self.news_idx = 0
        self.draw_dynamic()

    def ev_busy(self, flag):
        self.busy = flag
        if flag:
            self.set_action(self.mode, False)

    def ev_ready(self, arg):
        version, offline = arg
        self.busy = False
        self.progress = None
        self.ready_text = f"พร้อมเริ่มเกม v{version}" + (" (ออฟไลน์)" if offline else "")
        self.set_status(self.ready_text, FG, "")
        self.set_action("start", True)
        self.refresh_running_ui()

    def ev_need(self, arg):
        """ต้องโหลด/อัปเดตไฟล์เกม — รอให้ผู้เล่นกดเอง ยังไม่โหลดอะไรทั้งนั้น"""
        version, n_files, size, installed = arg
        self.busy = False
        self.progress = None
        if installed:
            self.mode_label = "อัปเดตเกม"
            title = f"มีอัปเดตใหม่ v{version}"
        else:
            self.mode_label = "โหลดเกม"
            title = f"ยังไม่ได้ติดตั้งเกม (v{version})"
        self.set_status(title, ACCENT,
                        f"{n_files} ไฟล์ • ขนาดดาวน์โหลดประมาณ {core.fmt_size(size)} "
                        f"— กดปุ่มเพื่อเริ่มโหลด")
        self.set_action("download", True)

    def ev_maint(self, msg):
        self.busy = False
        self.progress = None
        self.set_status("ปิดปรับปรุงชั่วคราว", ACCENT, msg)
        self.set_action("retry", True)

    def ev_error(self, msg):
        self.busy = False
        self.progress = None
        self.set_status(msg, ERR, "กด 'ลองใหม่' หรือดูรายละเอียดในไฟล์ launcher.log")
        self.set_action("retry", True)

    # ------------------------------------------------------------ ปุ่มต่างๆ
    def on_action(self):
        if not self.btn_enabled:
            return
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
        self.game_running = True
        self.set_action("start", True)
        self.refresh_running_ui()
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
        win.geometry(f"+{self.root.winfo_x() + 360}+{self.root.winfo_y() + 200}")
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
        if download and core.is_game_running(Path(self.settings.install_dir)):
            messagebox.showinfo(config.APP_NAME, "เกมกำลังทำงานอยู่ กรุณาปิดเกมก่อนโหลด/อัปเดต/ซ่อมแซมไฟล์")
            return
        self.last_flow = (repair, download)
        self.busy = True
        self.cancel = threading.Event()
        self.progress = 0.0
        self.detail = ""
        self.draw_status()
        self.set_action(self.mode, False)
        threading.Thread(target=self.flow, args=(repair, download, self.cancel),
                         daemon=True).start()

    def load_background(self):
        """พื้นหลังโหลดจาก site/background.png ใน repo (เปลี่ยนรูปได้โดยไม่ต้อง build ใหม่)"""
        try:
            data = core.http_get(config.RAW_BASE + "background.png", bust_cache=True)
            dest = core.data_dir() / "background.png"
            if not dest.exists() or dest.read_bytes() != data:
                dest.write_bytes(data)
                self.post("bg", str(dest))
        except Exception as e:  # noqa: BLE001
            core.log(f"โหลดพื้นหลังไม่ได้: {e}")

    def load_news(self):
        self.load_background()
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
                          "date": str(it.get("date", "")),
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
