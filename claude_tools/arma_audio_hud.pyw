"""ARMA AUDIO HUD - poste de commande tactique pour les profils Equalizer APO.

Lancement : pythonw arma_audio_hud.pyw [--minimized]
Raccourcis globaux (fonctionnent en jeu) : Ctrl+Shift+F1..F12.
Pilote eqctl.py (meme dossier) : aucune dependance externe.
"""
import ctypes
import ctypes.wintypes as wt
import io
import json
import math
import os
import queue
import random
import struct
import subprocess
import sys
import threading
import time
import tkinter as tk
import wave
import winsound
import zlib
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import eqctl as E  # noqa: E402

APP = "ARMA AUDIO HUD"
SETTINGS = HERE / "hud_settings.json"
SOUNDS = HERE / "sounds"
ICON_ICO = HERE / "arma_audio_hud.ico"
ICON_PNG = HERE / "arma_audio_hud.png"
STARTUP_LNK = Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup\Arma Audio HUD.lnk"

C = dict(bg="#080c09", panel="#0e1410", panel2="#131b15", line="#1d2920", grid="#152019",
         text="#d9e5d3", dim="#7b8b78", faint="#36443a", green="#8fe36b", amber="#ffb547",
         red="#ff5a4e", cyan="#59d0ff", violet="#c38bff", olive="#a8c060")

HEADSET = [
    dict(cmd="pas", name="RADAR", sub="Pas, buissons, rechargements", key="F1", col="green",
         file="01_wallhack_radar.txt", glyph="\u25ce"),
    dict(cmd="clutch", name="CLUTCH", sub="1vX, micro-bruits vitaux", key="F2", col="cyan",
         file="02_clutch_surgeon.txt", glyph="\u271a"),
    dict(cmd="chill", name="MARATHON", sub="4h+ sans fatigue", key="F3", col="amber",
         file="03_long_session_comfort.txt", glyph="\u25f7"),
    dict(cmd="solo", name="CIN\u00c9MA", sub="Basses, musique, films", key="F4", col="violet",
         file="04_cinematic_solo.txt", glyph="\u266b"),
    dict(cmd="perso", name="PERSO", sub="Ton r\u00e9glage sauv\u00e9", key="F11", col="olive",
         file="07_perso.txt", glyph="\u2605"),
]
SPEAKERS = [
    dict(cmd="enceintes", name="PROPRE", sub="Propre, voix claires", key="F5", col="green",
         file="05_speakers_clean.txt", glyph="\u25a3"),
    dict(cmd="nuit", name="NUIT", sub="Faible volume", key="F6", col="cyan",
         file="06_speakers_night_mode.txt", glyph="\u263e"),
    dict(cmd="spkneutre", name="NEUTRE", sub="Son d'origine", key="", col="dim",
         file="neutral.txt", glyph="\u25cb"),
]
COMPLAINTS = [
    ("H\u00e9licos / v\u00e9hicules", [(130, -3, 0.8, "helico")]),
    ("Mes buissons", [(4300, -1.5, 2.5, "buissons"), (5500, -2, 2.0, "feuillage")]),
    ("Pas de loin", [(1800, 1.5, 1.5, "pas-loin")]),
    ("Tirs qui claquent", [(7500, -2, 2.0, "tirs")]),
    ("Crack des balles", [(7500, 1.5, 2.0, "crack")]),
    ("\u00c9cho int\u00e9rieur", [(320, -1.5, 1.8, "reverb"), (500, -1.5, 1.4, "boite")]),
    ("Radio / VON", [(1500, 1.5, 1.2, "radio")]),
    ("Son \u00e9touff\u00e9", [(5000, 1.5, 1.0, "air")]),
]
HOTKEYS = [(0x70, "pas"), (0x71, "clutch"), (0x72, "chill"), (0x73, "solo"), (0x74, "enceintes"),
           (0x75, "nuit"), (0x76, "mal"), (0x77, "plusdepas"), (0x78, "bypass"), (0x79, "status"),
           (0x7A, "perso"), (0x7B, "reset")]
HOTKEY_HELP = [("F1", "Mode RADAR (pas)"), ("F2", "Mode CLUTCH"), ("F3", "Mode MARATHON"),
               ("F4", "Mode CIN\u00c9MA"), ("F5", "Enceintes propre"), ("F6", "Enceintes nuit"),
               ("F7", "Mal aux oreilles (-2.5 dB, 3-7 kHz)"), ("F8", "Plus de pas (+1.5 dB)"),
               ("F9", "A/B : son d'origine / profil"), ("F10", "Statut en surimpression"),
               ("F11", "Profil PERSO"), ("F12", "Reset (tout neutre)")]

FILE_INFO = {m["file"]: m for m in HEADSET + SPEAKERS}

# ---------------------------------------------------------------- utilitaires
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass
U32 = ctypes.windll.user32
K32 = ctypes.windll.kernel32


def col(name):
    return C.get(name, name)


def mix(c1, c2, t):
    a = [int(col(c1)[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(col(c2)[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def load_settings():
    d = {"sound": True, "toast": True, "view": "applied"}
    try:
        d.update(json.loads(SETTINGS.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return d


def save_settings(d):
    try:
        SETTINGS.write_text(json.dumps(d, indent=2), encoding="utf-8")
    except OSError:
        pass


def single_instance():
    h = K32.CreateMutexW(None, False, "Local\\ArmaAudioHUD_Mutex")
    if K32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        hwnd = U32.FindWindowW(None, APP)
        if hwnd:
            U32.ShowWindow(hwnd, 9)
            U32.SetForegroundWindow(hwnd)
        sys.exit(0)
    return h


# ---------------------------------------------------------------- sons radio
def make_sounds():
    SOUNDS.mkdir(exist_ok=True)
    sr = 44100
    rnd = random.Random(42)

    def render(segs):
        out, lp = [], 0.0
        for kind, dur in segs:
            n = int(sr * dur)
            for i in range(n):
                env = min(1.0, i / (sr * 0.004), (n - i) / (sr * 0.015))
                if kind == "n":
                    lp += 0.45 * ((rnd.random() * 2 - 1) - lp)
                    s = lp * 0.9
                elif kind == 0:
                    s = 0.0
                else:
                    ph = 2 * math.pi * kind * i / sr
                    s = 0.55 * math.sin(ph) + 0.15 * math.sin(2 * ph) + 0.06 * math.sin(3 * ph)
                out.append(s * env)
        return out

    defs = {
        "mode.wav": [("n", 0.045), (0, 0.015), (880, 0.06), (1320, 0.09)],
        "up.wav": [(660, 0.05), (990, 0.08)],
        "down.wav": [(990, 0.05), (660, 0.08)],
        "ab.wav": [(1250, 0.03)],
        "reset.wav": [("n", 0.03), (990, 0.06), (740, 0.06), (520, 0.1)],
        "status.wav": [("n", 0.04), (1100, 0.04), (0, 0.04), (1100, 0.04)],
        "save.wav": [(660, 0.05), (880, 0.05), (1320, 0.12)],
        "error.wav": [(220, 0.18)],
    }
    for name, segs in defs.items():
        p = SOUNDS / name
        if p.exists():
            continue
        data = render(segs)
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s)) * 0.16 * 32767)) for s in data))


# ---------------------------------------------------------------- icone radar
def _radar_pixel(u, v):
    r = math.hypot(u, v)
    if r > 0.985:
        return (0, 0, 0, 0)
    if r > 0.90:
        k = (r - 0.90) / 0.085
        return (int(40 + 30 * (1 - k)), int(56 + 40 * (1 - k)), int(34 + 14 * (1 - k)), 255)
    base = (6, int(20 + 14 * (1 - r)), 10)
    g = 0.0
    for ring in (0.3, 0.6, 0.88):
        g = max(g, max(0.0, 1 - abs(r - ring) / 0.014) * 0.55)
    if abs(u) < 0.009 or abs(v) < 0.009:
        g = max(g, 0.35)
    ang = math.degrees(math.atan2(-v, u)) % 360
    delta = (45 - ang) % 360
    if delta < 80:
        g = max(g, (1 - delta / 80) ** 1.6 * 0.75)
    if delta < 1.6 or delta > 358.4:
        g = 1.0
    rgb = [base[0] + (143 - base[0]) * g, base[1] + (227 - base[1]) * g, base[2] + (107 - base[2]) * g]
    for bx, by, br, bc in ((0.33, -0.30, 0.085, (255, 181, 71)), (-0.42, 0.28, 0.06, (143, 227, 107)),
                           (0.0, 0.0, 0.045, (217, 229, 211))):
        d = math.hypot(u - bx, v - by)
        if d < br:
            rgb = list(bc)
        elif d < br * 2.2:
            t = (1 - (d - br) / (br * 1.2)) * 0.45
            rgb = [rgb[i] + (bc[i] - rgb[i]) * t for i in range(3)]
    return (int(rgb[0]), int(rgb[1]), int(rgb[2]), 255)


def _render_icon(size, ss):
    n = size * ss
    px = bytearray()
    rows = []
    for y in range(n):
        rows.append([_radar_pixel((x + 0.5) / n * 2 - 1, (y + 0.5) / n * 2 - 1) for x in range(n)])
    for y in range(size):
        for x in range(size):
            acc = [0, 0, 0, 0]
            for yy in range(ss):
                for xx in range(ss):
                    p = rows[y * ss + yy][x * ss + xx]
                    a = p[3]
                    acc[0] += p[0] * a
                    acc[1] += p[1] * a
                    acc[2] += p[2] * a
                    acc[3] += a
            a = acc[3]
            if a:
                px += bytes((acc[0] // a, acc[1] // a, acc[2] // a, a // (ss * ss)))
            else:
                px += b"\x00\x00\x00\x00"
    return bytes(px)


def _png(w, h, rgba):
    raw = b"".join(b"\x00" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def make_icon():
    if ICON_ICO.exists() and ICON_PNG.exists():
        return
    imgs = []
    for size, ss in ((256, 2), (64, 4), (48, 4), (32, 4), (16, 4)):
        png = _png(size, size, _render_icon(size, ss))
        imgs.append((size, png))
        if size == 256:
            ICON_PNG.write_bytes(png)
    off = 6 + 16 * len(imgs)
    head, data = struct.pack("<HHH", 0, 1, len(imgs)), b""
    for s, png in imgs:
        head += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(png), off + len(data))
        data += png
    ICON_ICO.write_bytes(head + data)


def make_shortcut(lnk, minimized=False):
    pyw = Path(sys.executable).with_name("pythonw.exe")
    if not pyw.exists():
        pyw = Path(sys.executable)
    args = '"%s"%s' % (HERE / "arma_audio_hud.pyw", " --minimized" if minimized else "")
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');$s.TargetPath='%s';"
          "$s.Arguments='%s';$s.WorkingDirectory='%s';$s.IconLocation='%s,0';"
          "$s.Description='Poste de commande audio Arma Reforger';$s.Save()"
          % (lnk, pyw, args, HERE, ICON_ICO))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], creationflags=0x08000000, check=False)


# ---------------------------------------------------------------- raccourcis globaux
class Hotkeys(threading.Thread):
    def __init__(self, q):
        super().__init__(daemon=True)
        self.q, self.tid, self.failed = q, None, []
        self.ready = threading.Event()

    def run(self):
        self.tid = K32.GetCurrentThreadId()
        for i, (vk, action) in enumerate(HOTKEYS, start=1):
            if not U32.RegisterHotKey(None, i, 0x0002 | 0x0004 | 0x4000, vk):  # CTRL|SHIFT|NOREPEAT
                self.failed.append(action)
        self.ready.set()
        msg = wt.MSG()
        while U32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312 and 1 <= msg.wParam <= len(HOTKEYS):
                self.q.put(HOTKEYS[msg.wParam - 1][1])
        for i in range(1, len(HOTKEYS) + 1):
            U32.UnregisterHotKey(None, i)

    def stop(self):
        if self.tid:
            U32.PostThreadMessageW(self.tid, 0x0012, 0, 0)


# ---------------------------------------------------------------- widgets
class HudCard(tk.Canvas):
    def __init__(self, master, S, w, h, title, sub="", key="", color="green", glyph="",
                 command=None, on_press=None, on_release=None, size=13):
        super().__init__(master, width=w, height=h, bg=C["bg"], highlightthickness=0, bd=0, cursor="hand2")
        self.S, self.w, self.h = S, w, h
        self.title, self.sub, self.key, self.color, self.glyph, self.size = title, sub, key, col(color), glyph, size
        self.active = self.hover = self.pressed = self.disabled = False
        self.command, self.on_press, self.on_release = command, on_press, on_release
        self.bind("<Enter>", lambda e: self._set(hover=True))
        self.bind("<Leave>", lambda e: self._leave())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.draw()

    def _set(self, **kw):
        changed = any(getattr(self, k) != v for k, v in kw.items())
        for k, v in kw.items():
            setattr(self, k, v)
        if changed:
            self.draw()

    def _leave(self):
        if self.pressed and self.on_release:
            self.on_release()
        self._set(hover=False, pressed=False)

    def _press(self, _e):
        self._set(pressed=True)
        if self.on_press:
            self.on_press()

    def _release(self, _e):
        was = self.pressed
        self._set(pressed=False)
        if self.on_release and was:
            self.on_release()
        elif self.command and was and not self.disabled:
            self.command()

    def draw(self):
        S, w, h, c = self.S, self.w, self.h, self.color
        self.delete("all")
        if self.disabled:
            c = C["faint"]
        on = self.active or self.pressed
        fill = mix(c, C["bg"], 0.80) if on else (C["panel2"] if self.hover else C["panel"])
        border = c if on else (mix(c, C["bg"], 0.35) if self.hover else C["line"])
        lw = max(1, round(S))
        self.create_rectangle(lw, lw, w - lw - 1, h - lw - 1, fill=fill, outline=border, width=lw)
        L, t = int(9 * S), max(2, round(2 * S))
        bc = c if (on or self.hover) else C["faint"]
        for x, y, dx, dy in ((1, 1, 1, 1), (w - 2, 1, -1, 1), (1, h - 2, 1, -1), (w - 2, h - 2, -1, -1)):
            self.create_line(x, y, x + dx * L, y, fill=bc, width=t)
            self.create_line(x, y, x, y + dy * L, fill=bc, width=t)
        if self.active:
            self.create_rectangle(lw, lw, w - lw - 1, int(4 * S), fill=c, outline="")
        tx = int(12 * S)
        tc = C["text"] if not self.disabled else C["faint"]
        ty = int(h * 0.40) if self.sub else h // 2
        if self.glyph:
            self.create_text(tx, ty, text=self.glyph, anchor="w", fill=c,
                             font=("Segoe UI Symbol", int(self.size * 1.15)))
            tx2 = tx + int(self.size * 2.1 * S)
        else:
            tx2 = tx
        self.create_text(tx2, ty, text=self.title, anchor="w", fill=tc,
                         font=("Bahnschrift SemiBold", self.size))
        if self.sub:
            self.create_text(tx, int(h * 0.62), text=self.sub, anchor="nw", fill=C["dim"],
                             font=("Bahnschrift", 8 if self.size < 12 else 9), width=w - tx - int(6 * S))
        if self.key:
            self.create_text(w - int(7 * S), int(7 * S), text=self.key, anchor="ne",
                             fill=c if on else C["faint"], font=("Consolas", 7))

    def set_active(self, v):
        self._set(active=v)

    def set_disabled(self, v):
        self._set(disabled=v)


class Toast:
    """Surimpression HUD : toujours au premier plan, ne prend jamais le focus, clics traversants."""

    def __init__(self, root, S):
        self.S = S
        self.win = tk.Toplevel(root)
        self.win.title("ARMA AUDIO HUD overlay")
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.0)
        self.W, self.H = int(560 * S), int(100 * S)
        sw = root.winfo_screenwidth()
        self.x, self.y = (sw - self.W) // 2, int(36 * S)
        self.win.geometry("%dx%d+%d+%d" % (self.W, self.H, self.x, self.y))
        self.cv = tk.Canvas(self.win, width=self.W, height=self.H, bg=C["bg"], highlightthickness=0)
        self.cv.pack()
        self.win.update_idletasks()
        self.hwnd = U32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
        ex = U32.GetWindowLongW(self.hwnd, -20)
        U32.SetWindowLongW(self.hwnd, -20, ex | 0x08000000 | 0x80 | 0x20 | 0x80000 | 0x8)
        self.job, self.alpha = None, 0.0

    def show(self, title, sub, color="green"):
        c, S, W, H, cv = col(color), self.S, self.W, self.H, self.cv
        cv.delete("all")
        cv.create_rectangle(0, 0, W - 1, H - 1, fill=C["panel"], outline=mix(c, C["bg"], 0.3), width=max(1, round(S)))
        cv.create_rectangle(0, 0, int(7 * S), H, fill=c, outline="")
        L, t = int(14 * S), max(2, round(2 * S))
        for x, y, dx, dy in ((1, 1, 1, 1), (W - 2, 1, -1, 1), (1, H - 2, 1, -1), (W - 2, H - 2, -1, -1)):
            cv.create_line(x, y, x + dx * L, y, fill=c, width=t)
            cv.create_line(x, y, x, y + dy * L, fill=c, width=t)
        cv.create_text(int(24 * S), int(14 * S), text="ARMA AUDIO HUD  //  " + time.strftime("%H:%M:%S"),
                       anchor="nw", fill=C["dim"], font=("Consolas", 8))
        cv.create_text(int(24 * S), int(30 * S), text=title, anchor="nw", fill=c,
                       font=("Bahnschrift SemiBold", 19))
        cv.create_text(int(24 * S), int(64 * S), text=sub, anchor="nw", fill=C["text"],
                       font=("Bahnschrift", 10), width=W - int(40 * S))
        U32.SetWindowPos(self.hwnd, -1, self.x, self.y, 0, 0, 0x0001 | 0x0010 | 0x0040)  # TOPMOST, NOSIZE|NOACTIVATE|SHOW
        if self.job:
            self.win.after_cancel(self.job)
        self._fade(0.95, 0.19, hold=2200)

    def _fade(self, target, step, hold=0):
        self.alpha = min(target, self.alpha + step) if step > 0 else max(target, self.alpha + step)
        self.win.attributes("-alpha", self.alpha)
        if abs(self.alpha - target) > 1e-3:
            self.job = self.win.after(16, self._fade, target, step, hold)
        elif hold:
            self.job = self.win.after(hold, self._fade, 0.0, -0.06)
        else:
            self.job = None


# ---------------------------------------------------------------- application
class HUD:
    def __init__(self, minimized=False):
        self.settings = load_settings()
        self.root = tk.Tk()
        self.root.withdraw()
        self.S = S = self.root.winfo_fpixels("1i") / 96.0
        self.root.title(APP)
        self.root.configure(bg=C["bg"])
        self.root.resizable(False, False)
        try:
            self._icon = tk.PhotoImage(file=str(ICON_PNG))
            self.root.iconphoto(True, self._icon)
        except tk.TclError:
            pass
        self.q = queue.Queue()
        self.hk = Hotkeys(self.q)
        self.hk.start()
        self.hk.ready.wait(2)
        self.log_lines = []
        self.curve_shown = None
        self.curve_target = None
        self.sweep = 0.0
        self.blips = []
        self.state_mtime = 0
        self.ab_hold = False
        self._build()
        self.toast = Toast(self.root, S)
        self.refresh(animate=False)
        self.root.update_idletasks()
        w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        sx, sy = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry("+%d+%d" % (max(0, (sx - w) // 2), max(0, (sy - h) // 2 - int(20 * S))))
        self.root.deiconify()
        self._dark_titlebar()
        if minimized:
            self.root.iconify()
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.log("Poste op\u00e9rationnel \u00b7 hotkeys actifs"
                 if not self.hk.failed else "Raccourcis indisponibles : " + ", ".join(self.hk.failed))
        self.root.after(33, self.tick_radar)
        self.root.after(50, self.poll)
        self.root.after(1000, self.tick_clock)

    # ------------------------------------------------------------ construction
    def _dark_titlebar(self):
        try:
            hwnd = U32.GetParent(self.root.winfo_id())
            v = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(v), 4)
            cap = ctypes.c_int(0x00090c08)  # COLORREF BGR : fond de la barre de titre
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(cap), 4)
        except Exception:
            pass

    def _label(self, parent, text, size=9, color="dim", font="Bahnschrift", **kw):
        return tk.Label(parent, text=text, bg=kw.pop("bg", C["bg"]), fg=col(color), font=(font, size), **kw)

    def _section(self, parent, text):
        f = tk.Frame(parent, bg=C["bg"])
        self._label(f, "\u25b8 " + text, 9, "olive", "Bahnschrift SemiBold").pack(side="left")
        tk.Frame(f, bg=C["line"], height=max(1, round(self.S))).pack(side="left", fill="x", expand=True,
                                                                     padx=(int(8 * self.S), 0))
        return f

    def _build(self):
        S, r = self.S, self.root
        P = int(16 * S)
        # --- en-tete
        head = tk.Frame(r, bg=C["bg"])
        head.pack(fill="x", padx=P, pady=(int(12 * S), int(6 * S)))
        left = tk.Frame(head, bg=C["bg"])
        left.pack(side="left")
        self._label(left, "\u25e2 ARMA AUDIO HUD", 22, "text", "Bahnschrift SemiBold").pack(anchor="w")
        self._label(left, "POSTE DE COMMANDE AUDIO  \u00b7  CASQUE  \u00b7  ENCEINTES  \u00b7  EQUALIZER APO",
                    8, "dim", "Consolas").pack(anchor="w")
        right = tk.Frame(head, bg=C["bg"])
        right.pack(side="right")
        self.clock = self._label(right, "", 20, "green", "Consolas")
        self.clock.pack(anchor="e")
        self.link = self._label(right, "", 8, "dim", "Consolas")
        self.link.pack(anchor="e")
        tk.Frame(r, bg=C["line"], height=max(1, round(S))).pack(fill="x", padx=P)

        body = tk.Frame(r, bg=C["bg"])
        body.pack(fill="both", padx=P, pady=int(10 * S))
        lcol = tk.Frame(body, bg=C["bg"])
        lcol.pack(side="left", fill="y", anchor="n")
        rcol = tk.Frame(body, bg=C["bg"])
        rcol.pack(side="left", fill="both", padx=(int(16 * S), 0), anchor="n")

        # --- colonne gauche : radar + jauges + journal
        self.RW = int(300 * S)
        self.radar = tk.Canvas(lcol, width=self.RW, height=self.RW, bg=C["bg"], highlightthickness=0)
        self.radar.pack()
        self._radar_static()
        self.mode_lbl = self._label(lcol, "", 24, "green", "Bahnschrift SemiBold")
        self.mode_lbl.pack(pady=(int(4 * S), 0))
        self.mode_sub = self._label(lcol, "", 9, "dim")
        self.mode_sub.pack()
        self.gauge = tk.Canvas(lcol, width=self.RW, height=int(112 * S), bg=C["bg"], highlightthickness=0)
        self.gauge.pack(pady=(int(10 * S), 0))
        self._section(lcol, "JOURNAL RADIO").pack(fill="x", pady=(int(8 * S), int(4 * S)))
        self.journal = tk.Canvas(lcol, width=self.RW, height=int(104 * S), bg=C["panel"],
                                 highlightthickness=max(1, round(S)), highlightbackground=C["line"])
        self.journal.pack()

        # --- colonne droite : courbe + commandes
        top = tk.Frame(rcol, bg=C["bg"])
        top.pack(fill="x")
        self._section(top, "SIGNATURE SPECTRALE  \u00b7  CASQUE").pack(side="left", fill="x", expand=True)
        self.view_btns = {}
        for key, txt in (("applied", "COURBE APPLIQU\u00c9E"), ("acoustic", "EFFET vs HARMAN")):
            b = self._label(top, " " + txt + " ", 8, "dim", "Consolas", cursor="hand2", bg=C["panel"])
            b.pack(side="left", padx=(int(6 * S), 0))
            b.bind("<Button-1>", lambda e, k=key: self.set_view(k))
            self.view_btns[key] = b
        self.CW, self.CH = int(790 * S), int(228 * S)
        self.curve = tk.Canvas(rcol, width=self.CW, height=self.CH, bg=C["panel"],
                               highlightthickness=max(1, round(S)), highlightbackground=C["line"])
        self.curve.pack(pady=(int(6 * S), int(10 * S)))

        self._section(rcol, "MODES CASQUE").pack(fill="x", pady=(0, int(6 * S)))
        row = tk.Frame(rcol, bg=C["bg"])
        row.pack(fill="x")
        gap = int(8 * S)
        cw = (self.CW - gap * 4) // 5
        self.hcards = {}
        for i, m in enumerate(HEADSET):
            c = HudCard(row, S, cw, int(78 * S), m["name"], m["sub"], "CTRL+SHIFT+" + m["key"], m["col"],
                        m["glyph"], command=lambda m=m: self.action(m["cmd"]), size=13)
            c.pack(side="left", padx=(0 if i == 0 else gap, 0))
            self.hcards[m["file"]] = c

        mid = tk.Frame(rcol, bg=C["bg"])
        mid.pack(fill="x", pady=(int(12 * S), 0))
        spk = tk.Frame(mid, bg=C["bg"])
        spk.pack(side="left", anchor="n")
        live = tk.Frame(mid, bg=C["bg"])
        live.pack(side="left", anchor="n", padx=(int(16 * S), 0))
        sw_ = int(118 * S)
        self._section(spk, "ENCEINTES").pack(fill="x", pady=(0, int(6 * S)))
        srow = tk.Frame(spk, bg=C["bg"])
        srow.pack()
        self.scards = {}
        for i, m in enumerate(SPEAKERS):
            c = HudCard(srow, S, sw_, int(64 * S), m["name"], m["sub"], m["key"], m["col"], m["glyph"],
                        command=lambda m=m: self.action(m["cmd"]), size=10)
            c.pack(side="left", padx=(0 if i == 0 else int(6 * S), 0))
            self.scards[m["file"]] = c
        lw_ = self.CW - 3 * sw_ - 2 * int(6 * S) - int(16 * S)
        self._section(live, "R\u00c9GLAGES EN DIRECT").pack(fill="x", pady=(0, int(6 * S)))
        lrow = tk.Frame(live, bg=C["bg"])
        lrow.pack()
        bw = (lw_ - 3 * int(6 * S)) // 4
        for i, (t, s, k, c, g, cmd) in enumerate((
                ("+ DE PAS", "+1.5 dB bottes", "F8", "green", "\u25b2", "plusdepas"),
                ("MAL", "-2.5 dB 3-7k", "F7", "red", "\u25bc", "mal"),
                ("ANNULER", "derni\u00e8re corr.", "", "amber", "\u21b6", "undo"),
                ("EFFACER", "tout ajust.", "", "dim", "\u2715", "clearadj"))):
            HudCard(lrow, S, bw, int(64 * S), t, s, k, c, g, command=lambda cmd=cmd: self.action(cmd),
                    size=9).pack(side="left", padx=(0 if i == 0 else int(6 * S), 0))

        self._section(rcol, "SIGNALER UN PROBL\u00c8ME  \u00b7  CORRECTION AUTOMATIQUE").pack(
            fill="x", pady=(int(12 * S), int(6 * S)))
        crow = tk.Frame(rcol, bg=C["bg"])
        crow.pack(fill="x")
        chw = (self.CW - 3 * int(6 * S)) // 4
        for i, (label, filt) in enumerate(COMPLAINTS):
            c = HudCard(crow, S, chw, int(34 * S), label, "", "", "amber", "",
                        command=lambda l=label, f=filt: self.complaint(l, f), size=10)
            c.grid(row=i // 4, column=i % 4, padx=(0 if i % 4 == 0 else int(6 * S), 0), pady=(0, int(6 * S)))

        # --- pied
        tk.Frame(r, bg=C["line"], height=max(1, round(S))).pack(fill="x", padx=P)
        foot = tk.Frame(r, bg=C["bg"])
        foot.pack(fill="x", padx=P, pady=(int(10 * S), int(14 * S)))
        self.ab = HudCard(foot, S, int(250 * S), int(52 * S), "A/B  MAINTENIR",
                          "Son d'origine tant que tu appuies", "C+S+F9", "amber", "\u25d1",
                          on_press=self.ab_press, on_release=self.ab_release, size=12)
        self.ab.pack(side="left")
        HudCard(foot, S, int(200 * S), int(52 * S), "SAUVER PERSO", "Fige profil + ajustements", "",
                "olive", "\u2605", command=lambda: self.action("save"), size=12).pack(side="left", padx=(int(8 * S), 0))
        HudCard(foot, S, int(150 * S), int(52 * S), "RESET", "Tout neutre", "C+S+F12", "red", "\u23fb",
                command=lambda: self.action("reset"), size=12).pack(side="left", padx=(int(8 * S), 0))
        opts = tk.Frame(foot, bg=C["bg"])
        opts.pack(side="right")
        self.opt_lbls = {}
        for key, txt in (("sound", "BIPS RADIO"), ("toast", "SURIMPRESSION"), ("startup", "LANCER AVEC WINDOWS")):
            l = self._label(opts, "", 9, "dim", "Consolas", cursor="hand2")
            l.pack(anchor="e")
            l.bind("<Button-1>", lambda e, k=key: self.toggle_opt(k))
            self.opt_lbls[key] = (l, txt)
        h = self._label(opts, "[ ? ] RACCOURCIS CLAVIER", 9, "olive", "Consolas", cursor="hand2")
        h.pack(anchor="e", pady=(int(4 * S), 0))
        h.bind("<Button-1>", lambda e: self.show_help())
        self._draw_opts()
        self._draw_view_btns()

    # ------------------------------------------------------------ radar
    def _radar_static(self):
        cv, W = self.radar, self.RW
        cx = cy = W / 2
        R = W / 2 - 18 * self.S
        self.R, self.cx, self.cy = R, cx, cy
        cv.create_oval(cx - R - 6 * self.S, cy - R - 6 * self.S, cx + R + 6 * self.S, cy + R + 6 * self.S,
                       outline=C["faint"], width=max(1, round(2 * self.S)))
        cv.create_oval(cx - R, cy - R, cx + R, cy + R, fill="#07110a", outline=mix("green", "bg", 0.4))
        for k in (0.33, 0.66):
            cv.create_oval(cx - R * k, cy - R * k, cx + R * k, cy + R * k, outline=mix("green", "bg", 0.75))
        cv.create_line(cx - R, cy, cx + R, cy, fill=mix("green", "bg", 0.82))
        cv.create_line(cx, cy - R, cx, cy + R, fill=mix("green", "bg", 0.82))
        for deg in range(0, 360, 10):
            a = math.radians(deg)
            l = 7 if deg % 30 == 0 else 3
            cv.create_line(cx + math.sin(a) * R, cy - math.cos(a) * R,
                           cx + math.sin(a) * (R - l * self.S), cy - math.cos(a) * (R - l * self.S),
                           fill=mix("green", "bg", 0.5))
        for f, txt in ((40, "40"), (100, "100"), (300, "300"), (1000, "1k"), (3000, "3k"), (8000, "8k")):
            a = self.f2ang(f)
            cv.create_text(cx + math.sin(a) * (R + 11 * self.S), cy - math.cos(a) * (R + 11 * self.S),
                           text=txt, fill=C["dim"], font=("Consolas", 7))

    @staticmethod
    def f2ang(f):
        return (math.log10(f) - math.log10(20)) / 3.0 * 2 * math.pi

    def tick_radar(self):
        cv, cx, cy, R = self.radar, self.cx, self.cy, self.R
        cv.delete("dyn")
        self.sweep = (self.sweep + 2 * math.pi / 95) % (2 * math.pi)
        mc = self.mode_color
        if not self.ab_hold and not self.bypass:
            base = 90 - math.degrees(self.sweep)  # angle Tk (anti-horaire depuis 3 h)
            n, seg = 22, 3.0
            for k in range(n - 1, -1, -1):  # trainee remanente derriere le faisceau
                cv.create_arc(cx - R, cy - R, cx + R, cy + R, start=base + k * seg, extent=seg + 0.6,
                              style="pieslice", fill=mix(mc, "#07110a", 0.45 + 0.55 * k / n), outline="",
                              tags="dyn")
            cv.create_line(cx, cy, cx + math.sin(self.sweep) * R, cy - math.cos(self.sweep) * R,
                           fill=mc, width=max(2, round(2 * self.S)), tags="dyn")
        now_a = self.sweep
        for ang, rad, size, bc in self.blips:
            age = (now_a - ang) % (2 * math.pi) / (2 * math.pi)
            t = min(0.85, age * 1.15)
            x, y = cx + math.sin(ang) * rad, cy - math.cos(ang) * rad
            c = mix(bc, "#07110a", t)
            cv.create_oval(x - size * 2, y - size * 2, x + size * 2, y + size * 2, outline=mix(bc, "#07110a", 0.6 + t * 0.4),
                           tags="dyn")
            cv.create_oval(x - size, y - size, x + size, y + size, fill=c, outline="", tags="dyn")
        if self.bypass:
            cv.create_text(cx, cy, text="BYPASS\nSON D'ORIGINE", fill=C["amber"], justify="center",
                           font=("Bahnschrift SemiBold", 15), tags="dyn")
        cv.create_oval(cx - 3 * self.S, cy - 3 * self.S, cx + 3 * self.S, cy + 3 * self.S, fill=C["text"],
                       outline="", tags="dyn")
        self.root.after(33, self.tick_radar)

    # ------------------------------------------------------------ donnees & rendu
    def compute(self):
        st = E.load_state()
        hc = E.headset_chain(st)
        if self.settings["view"] == "acoustic":
            prof = E.parse_file(E.PROFILES / st["headset"], follow=False)
            curve = E.response(prof["filters"] + [f[:4] for f in hc["overlay"]], prof["xfeeds"])
        else:
            curve = hc["curve"]
        sp = E.parse_file(E.PROFILES / st["speakers"])
        spc = E.response(sp["filters"], sp["xfeeds"])
        return st, hc, curve, spc, E.needed_preamp(spc)

    def refresh(self, animate=True):
        try:
            self.state_mtime = E.STATE.stat().st_mtime
        except OSError:
            self.state_mtime = 0
        st, hc, curve, spc, sp_pre = self.compute()
        self.st, self.hc, self.spc = st, hc, spc
        self.bypass = bool(st.get("bypass"))
        info = FILE_INFO.get(st["headset"], dict(name="NEUTRE", sub="Aucun filtre", col="dim"))
        self.mode_color = col(info["col"]) if not self.bypass else C["amber"]
        self.mode_lbl.config(text=("A/B \u00b7 ORIGINE" if self.bypass else info["name"]), fg=self.mode_color)
        self.mode_sub.config(text=info["sub"] + "  \u00b7  " + st["headset"])
        for f, c in self.hcards.items():
            c.set_active(st["headset"] == f)
            c.set_disabled(not (E.PROFILES / f).exists())
        for f, c in self.scards.items():
            c.set_active(st["speakers"] == f)
        self.ab.set_active(self.bypass)
        # blips radar : boosts et coupes de la courbe appliquee
        self.blips = []
        flat = [0.0] * len(curve)
        disp = flat if self.bypass else curve
        for g, f in E.top_peaks(hc["curve"], 5):
            self.blips.append((self.f2ang(f), self.R * (0.25 + 0.7 * min(1, g / 10)), (3 + min(4, g / 2.5)) * self.S,
                               self.mode_color))
        inv = [-x for x in hc["curve"]]
        for g, f in E.top_peaks(inv, 3):
            if f > 60:
                self.blips.append((self.f2ang(f), self.R * (0.25 + 0.7 * min(1, g / 10)), 2.5 * self.S, C["red"]))
        if not self.blips or self.bypass:
            self.blips = [] if self.bypass else self.blips
        self.draw_gauge(st, hc, sp_pre)
        self.draw_link()
        self.curve_target = disp
        if not animate or self.curve_shown is None:
            self.curve_shown = list(disp)
            self.draw_curve()
        else:
            self.animate_curve(0)

    def animate_curve(self, i):
        start = self.curve_shown
        t = (i + 1) / 10.0
        e = 1 - (1 - t) ** 3
        self._anim_from = getattr(self, "_anim_from", None) if i else list(start)
        frm = self._anim_from
        self.curve_shown = [a + (b - a) * e for a, b in zip(frm, self.curve_target)]
        self.draw_curve()
        if i < 9:
            self.root.after(16, self.animate_curve, i + 1)

    def draw_curve(self):
        cv, S, W, H = self.curve, self.S, self.CW, self.CH
        cv.delete("all")
        L, Rm, T, B = int(42 * S), int(12 * S), int(22 * S), int(22 * S)
        lo, hi = -18.0, 12.0
        fx = lambda f: L + (math.log10(f) - math.log10(20)) / 3.0 * (W - L - Rm)
        fy = lambda d: T + (hi - max(lo, min(hi, d))) / (hi - lo) * (H - T - B)
        for f1, f2, txt, c, top in ((20, 150, "V\u00c9HICULES \u00b7 ARTILLERIE", "red", True),
                                    (1800, 5200, "PAS \u00b7 BUISSONS", "green", False),
                                    (6000, 9500, "TIRS", "amber", False)):
            cv.create_rectangle(fx(f1), T, fx(f2), H - B, fill=mix(c, C["panel"], 0.9), outline="")
            cv.create_text((fx(f1) + fx(f2)) / 2, (T + 18 * S) if top else (H - B - 7 * S), text=txt,
                           fill=mix(c, C["panel"], 0.35), font=("Consolas", 7))
        for d in range(-18, 13, 6):
            cv.create_line(L, fy(d), W - Rm, fy(d), fill=C["faint"] if d == 0 else C["grid"])
            cv.create_text(L - 6 * S, fy(d), text="%+d" % d if d else "0", anchor="e", fill=C["dim"],
                           font=("Consolas", 7))
        for f, t in ((20, "20"), (50, "50"), (100, "100"), (200, "200"), (500, "500"), (1000, "1k"),
                     (2000, "2k"), (5000, "5k"), (10000, "10k"), (20000, "20k")):
            cv.create_line(fx(f), T, fx(f), H - B, fill=C["grid"])
            cv.create_text(fx(f), H - B + 10 * S, text=t, fill=C["dim"], font=("Consolas", 7))
        G = E.GRID
        # enceintes (pointilles)
        pts = []
        for i in range(0, len(G), 3):
            pts += [fx(G[i]), fy(self.spc[i])]
        cv.create_line(*pts, fill=mix("cyan", "panel", 0.55), dash=(3, 4), width=1)
        # casque
        c = self.mode_color
        pts = []
        for i in range(0, len(G), 2):
            pts += [fx(G[i]), fy(self.curve_shown[i])]
        poly = [fx(G[0]), fy(0)] + pts + [fx(G[-1]), fy(0)]
        cv.create_polygon(*poly, fill=mix(c, C["panel"], 0.84), outline="")
        cv.create_line(*pts, fill=mix(c, C["panel"], 0.55), width=max(3, round(5 * S)))
        cv.create_line(*pts, fill=c, width=max(1, round(2 * S)))
        if not self.bypass:
            for g, f in E.top_peaks(self.curve_shown, 3):
                x, y = fx(f), fy(g)
                cv.create_polygon(x, y - 4 * S, x - 5 * S, y - 12 * S, x + 5 * S, y - 12 * S, fill=c, outline="")
                cv.create_text(x, y - 15 * S, text="%s %+.1f" % (E.fmt_f(f), g), anchor="s", fill=C["text"],
                               font=("Consolas", 8))
        else:
            cv.create_text(W / 2, H / 2 - 14 * S, text="BYPASS  \u00b7  SON D'ORIGINE DU CASQUE",
                           fill=C["amber"], font=("Bahnschrift SemiBold", 16))
        view = "courbe appliqu\u00e9e (correction casque incluse)" if self.settings["view"] == "applied" \
            else "effet ressenti au-dessus de la cible Harman"
        cv.create_text(L + 6 * S, 6 * S, text=view + "   \u2504\u2504 enceintes", anchor="nw", fill=C["dim"],
                       font=("Consolas", 7))

    def draw_gauge(self, st, hc, sp_pre):
        cv, S, W = self.gauge, self.S, self.RW
        cv.delete("all")

        def bar(y, label, val, vmax, color, text):
            cv.create_text(0, y, text=label, anchor="w", fill=C["dim"], font=("Consolas", 8))
            cv.create_text(W, y, text=text, anchor="e", fill=col(color), font=("Consolas", 8))
            y2, segs = y + 9 * S, 20
            sw = (W - (segs - 1) * 2 * S) / segs
            n = round(min(1, abs(val) / vmax) * segs)
            for i in range(segs):
                x = i * (sw + 2 * S)
                cv.create_rectangle(x, y2, x + sw, y2 + 7 * S, outline="",
                                    fill=col(color) if i < n else C["line"])
        pre = 0.0 if self.bypass else hc["total"]
        bar(6 * S, "PREAMP CASQUE", pre, 15, "cyan", "%.1f dB  \u00b7  ANTI-CLIPPING OK" % pre)
        bar(34 * S, "PLUS DE PAS", st["plus_db"], E.PLUS["ceil"], "green", "%+.1f / +%.0f dB" % (st["plus_db"], E.PLUS["ceil"]))
        bar(62 * S, "MAL AUX OREILLES", st["mal_db"], -E.MAL["floor"], "red", "%+.1f / %.0f dB" % (st["mal_db"], E.MAL["floor"]))
        notes = ", ".join(e.get("note", "adj") for e in st["extra"]) or "aucune"
        cv.create_text(0, 92 * S, text="CORRECTIONS : " + notes, anchor="w", fill=C["amber"] if st["extra"] else C["faint"],
                       font=("Consolas", 8), width=W)
        spn = FILE_INFO.get(st["speakers"], dict(name=st["speakers"]))["name"]
        cv.create_text(0, 106 * S, text="ENCEINTES : %s  \u00b7  Preamp %.1f dB" % (spn, sp_pre),
                       anchor="w", fill=C["dim"], font=("Consolas", 8))

    def draw_link(self):
        try:
            ok = "claude_active.txt" in (E.CONFIG / "config.txt").read_text(encoding="utf-8-sig")
        except OSError:
            ok = False
        hk = "HOTKEYS OK" if not self.hk.failed else "HOTKEYS %d KO" % len(self.hk.failed)
        self.link.config(text=("\u25cf APO LINK" if ok else "\u25cf APO LINK ABSENT") + "  \u00b7  " + hk,
                         fg=C["green"] if ok and not self.hk.failed else C["red"])

    def log(self, text):
        self.log_lines.insert(0, (time.strftime("%H:%M:%S"), text))
        self.log_lines = self.log_lines[:6]
        cv, S = self.journal, self.S
        cv.delete("all")
        for i, (t, s) in enumerate(self.log_lines):
            y = 8 * S + i * 16 * S
            cv.create_text(8 * S, y, text=t, anchor="nw", fill=C["olive"] if i == 0 else C["faint"],
                           font=("Consolas", 8))
            s = s if len(s) <= 37 else s[:36] + "…"
            cv.create_text(66 * S, y, text=s, anchor="nw", fill=C["text"] if i == 0 else C["dim"],
                           font=("Consolas", 8))

    # ------------------------------------------------------------ actions
    def run_eq(self, *args):
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = E.main([str(a) for a in args])
        except Exception as ex:  # noqa: BLE001
            return 1, "ERREUR : %s" % ex
        return rc, buf.getvalue().strip()

    def play(self, name):
        if self.settings["sound"]:
            p = SOUNDS / name
            if p.exists():
                winsound.PlaySound(str(p), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)

    def notify(self, title, sub, color):
        if self.settings["toast"]:
            self.toast.show(title, sub, color)

    def action(self, cmd, from_hotkey=False):
        rc, out = self.run_eq(cmd)
        self.refresh()
        st, hc = self.st, self.hc
        if rc:
            self.play("error.wav")
            self.notify("ACTION IMPOSSIBLE", out.replace("ERREUR : ", ""), "red")
            self.log(out)
            return
        pre = "Preamp %.1f dB" % hc["total"]
        if cmd in ("pas", "clutch", "chill", "solo", "perso"):
            m = FILE_INFO[st["headset"]]
            self.play("mode.wav")
            self.notify("MODE " + m["name"], "Casque  \u00b7  %s  \u00b7  %s" % (m["sub"], pre), m["col"])
            self.log("Casque -> " + m["name"])
        elif cmd in ("enceintes", "nuit", "spkneutre"):
            m = FILE_INFO[st["speakers"]]
            self.play("mode.wav")
            self.notify("ENCEINTES \u00b7 " + m["name"], "Enceintes  \u00b7  " + m["sub"], m["col"])
            self.log("Enceintes -> " + m["name"])
        elif cmd == "mal":
            self.play("down.wav")
            self.notify("\u25bc AIGUS AGRESSIFS %+.1f dB" % E.MAL["step"],
                        "Zone 3-7 kHz : total %+.1f dB  \u00b7  %s" % (st["mal_db"], pre), "red")
            self.log("Mal aux oreilles : 3-7 kHz %+.1f dB" % st["mal_db"])
        elif cmd == "plusdepas":
            self.play("up.wav")
            fr = " + ".join(E.fmt_f(f) for f in hc["steps"])
            self.notify("\u25b2 PLUS DE PAS %+.1f dB" % E.PLUS["step"],
                        "%s : total %+.1f dB  \u00b7  %s" % (fr, st["plus_db"], pre), "green")
            self.log("Plus de pas : %+.1f dB" % st["plus_db"])
        elif cmd == "bypass":
            self.play("ab.wav")
            self.notify("A/B \u00b7 SON D'ORIGINE" if self.bypass else "A/B \u00b7 PROFIL RESTAUR\u00c9",
                        "Ctrl+Shift+F9 pour basculer", "amber")
            self.log("A/B : " + ("origine" if self.bypass else "profil"))
        elif cmd == "status":
            self.play("status.wav")
            m = FILE_INFO.get(st["headset"], dict(name="NEUTRE", col="dim"))
            peaks = ", ".join("%s %+.1f" % (E.fmt_f(f), g) for g, f in E.top_peaks(hc["curve"], 3)) or "aucun"
            spn = FILE_INFO.get(st["speakers"], dict(name=st["speakers"]))["name"]
            self.notify("STATUT \u00b7 " + ("BYPASS" if self.bypass else m["name"]),
                        "%s  \u00b7  Enceintes : %s  \u00b7  Boosts : %s" % (pre, spn, peaks), m["col"])
        elif cmd == "reset":
            self.play("reset.wav")
            self.notify("RESET \u00b7 TOUT NEUTRE", "Casque et enceintes sans filtre", "red")
            self.log("Reset : tout neutre")
        elif cmd == "save":
            self.play("save.wav")
            self.notify("\u2605 PROFIL PERSO SAUVEGARD\u00c9", "07_perso.txt actif  \u00b7  Ctrl+Shift+F11  \u00b7  " + pre, "olive")
            self.log("Profil perso sauvegard\u00e9")
        elif cmd in ("undo", "clearadj"):
            self.play("down.wav")
            msg = out[3:] if out.startswith("OK ") else out
            self.notify("CORRECTION ANNUL\u00c9E" if cmd == "undo" else "AJUSTEMENTS EFFAC\u00c9S", msg[:90], "amber")
            self.log(msg)

    def complaint(self, label, filters):
        st = E.load_state()
        st["bypass"] = False
        for fc, g, q, note in filters:
            st["extra"].append({"fc": fc, "gain": g, "q": q, "note": note})
        hc = E.write_all(st)
        self.refresh()
        desc = " + ".join("%s %+.1f dB" % (E.fmt_f(fc), g) for fc, g, q, n in filters)
        self.play("up.wav" if filters[0][1] > 0 else "down.wav")
        self.notify("CORRECTION \u00b7 " + label.upper(), desc + "  \u00b7  Preamp %.1f dB  \u00b7  ANNULER pour revenir"
                    % hc["total"], "amber")
        self.log("%s : %s" % (label, desc))

    def ab_press(self):
        self.ab_hold = True
        self.run_eq("bypass", "on")
        self.play("ab.wav")
        self.refresh()

    def ab_release(self):
        self.ab_hold = False
        self.run_eq("bypass", "off")
        self.refresh()

    def set_view(self, k):
        self.settings["view"] = k
        save_settings(self.settings)
        self._draw_view_btns()
        self.refresh()

    def _draw_view_btns(self):
        for k, b in self.view_btns.items():
            on = self.settings["view"] == k
            b.config(fg=C["bg"] if on else C["dim"], bg=C["olive"] if on else C["panel"])

    def toggle_opt(self, k):
        if k == "startup":
            if STARTUP_LNK.exists():
                STARTUP_LNK.unlink()
                self.log("D\u00e9marrage auto d\u00e9sactiv\u00e9")
            else:
                make_shortcut(STARTUP_LNK, minimized=True)
                self.log("D\u00e9marrage auto activ\u00e9 (r\u00e9duit)")
        else:
            self.settings[k] = not self.settings[k]
            save_settings(self.settings)
            if k == "sound" and self.settings[k]:
                self.play("ab.wav")
        self._draw_opts()

    def _draw_opts(self):
        for k, (l, txt) in self.opt_lbls.items():
            on = STARTUP_LNK.exists() if k == "startup" else self.settings[k]
            l.config(text="%s  [%s]" % (txt, "ON " if on else "OFF"), fg=C["green"] if on else C["dim"])

    def show_help(self):
        S = self.S
        t = tk.Toplevel(self.root, bg=C["bg"])
        t.title("Raccourcis - " + APP)
        t.resizable(False, False)
        t.transient(self.root)
        self._label(t, "\u25e2 RACCOURCIS GLOBAUX", 16, "text", "Bahnschrift SemiBold").pack(
            anchor="w", padx=int(18 * S), pady=(int(14 * S), 0))
        self._label(t, "Actifs partout, y compris en jeu (Arma en plein \u00e9cran fen\u00eatr\u00e9 pour voir la surimpression).",
                    9, "dim").pack(anchor="w", padx=int(18 * S))
        g = tk.Frame(t, bg=C["bg"])
        g.pack(padx=int(18 * S), pady=int(12 * S))
        for i, (k, d) in enumerate(HOTKEY_HELP):
            self._label(g, "CTRL+SHIFT+" + k, 10, "green", "Consolas").grid(row=i, column=0, sticky="w", pady=1)
            self._label(g, d, 10, "text").grid(row=i, column=1, sticky="w", padx=(int(16 * S), 0))
        self._label(t, "Dans Claude Code : \u00e9cris simplement pas, clutch, mal, plusdepas, status...\n"
                       "ou d\u00e9cris ton probl\u00e8me (\u00ab j'entends trop les h\u00e9licos \u00bb).",
                    9, "olive").pack(anchor="w", padx=int(18 * S), pady=(0, int(14 * S)))

    # ------------------------------------------------------------ boucles
    def poll(self):
        try:
            while True:
                self.action(self.q.get_nowait(), from_hotkey=True)
        except queue.Empty:
            pass
        try:
            m = E.STATE.stat().st_mtime
            if m != self.state_mtime and not self.ab_hold:
                self.refresh()
                self.log("Synchro : changement re\u00e7u (Claude / externe)")
        except OSError:
            pass
        self.root.after(120, self.poll)

    def tick_clock(self):
        self.clock.config(text=time.strftime("%H:%M:%S"))
        self.root.after(1000, self.tick_clock)

    def quit(self):
        if self.ab_hold:
            self.run_eq("bypass", "off")
        self.hk.stop()
        self.root.destroy()

    def run(self):
        self.tick_clock()
        self.root.mainloop()


if __name__ == "__main__":
    _mutex = single_instance()
    make_sounds()
    make_icon()
    HUD(minimized="--minimized" in sys.argv).run()
