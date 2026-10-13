# /// script
# requires-python = ">=3.11"
# dependencies = ["sounddevice>=0.4"]
# ///
"""voice-chat floating ball: a small always-on-top mic button.

    uv run ball.pyw     # needs voice.py running (reads %APPDATA%/voice-chat/session.json)

Click to start recording, click again to stop and send. Drag to move (the spot is remembered).
The ball never takes focus, so the terminal keeps the keyboard. It exits by itself once voice.py
is gone. What you say arrives in Claude Code like typed text; the answer is in the terminal.

Each utterance goes to ONE session: by default the one in the terminal window that was in the
foreground when recording started (the ball finds each session's window from its Claude Code pid);
when that doesn't match one, voice.py picks the last target. Right-click lists the sessions to pin
one ("跟随焦点" = follow focus), and stops the server or closes the ball. After sending, a short note
next to the ball names the session it went to.

Colours: dark = ready, grey = Claude is not listening, red = recording, amber = transcribing.
Small dot: green = Claude listening, red = not.
"""

import ctypes
import io
import json
import os
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
import wave
from pathlib import Path

import sounddevice as sd

STATE = Path(os.environ.get("APPDATA", Path.home())) / "voice-chat"
SESSION_FILE = STATE / "session.json"
POS_FILE = STATE / "ball.json"
LOG = STATE / "ball.log"
RATE = 16000
KEY = "#010203"   # transparent colour around the circle

COLORS = {"ready": "#27272a", "away": "#71717a", "rec": "#dc2626", "busy": "#d97706", "error": "#dc2626"}

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.GetWindow.restype = user32.GetForegroundWindow.restype = kernel32.GetConsoleWindow.restype = ctypes.c_void_p
user32.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]


def log(*a):
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + " ".join(map(str, a)) + "\n")
    except OSError:
        pass


def session():
    s = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    return f"http://127.0.0.1:{s['port']}", s["token"]


def terminal_window(pid):
    """Top-level window of the terminal process `pid` runs in (a Windows Terminal window or conhost)."""
    kernel32.FreeConsole()
    if not kernel32.AttachConsole(pid):
        return None
    try:
        con = kernel32.GetConsoleWindow()
    finally:
        kernel32.FreeConsole()
    if not con:
        return None
    # Under Windows Terminal the console window is ConPTY's hidden one, owned by the terminal window.
    return user32.GetWindow(con, 4) or con   # GW_OWNER


def no_activate(win):
    """Make a window click-through for focus: it gets mouse clicks but never becomes active."""
    win.update_idletasks()
    hwnd = user32.GetParent(win.winfo_id())
    GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = -20, 0x08000000, 0x00000080
    user32.GetWindowLongPtrW.restype = user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
    return hwnd


class Ball:
    def __init__(self):
        self.base, self.token = session()
        self.root = root = tk.Tk()
        self.scale = root.winfo_fpixels("1i") / 96
        self.size = round(56 * self.scale)
        root.overrideredirect(True)
        root.attributes("-topmost", True, "-transparentcolor", KEY)
        root.configure(bg=KEY)
        self.cv = tk.Canvas(root, width=self.size, height=self.size, bg=KEY, highlightthickness=0)
        self.cv.pack()
        root.geometry(f"{self.size}x{self.size}+{self.load_pos()}")
        self.hwnd = no_activate(root)

        self.state = "away"       # ready / away / rec / busy / error
        self.listening = False
        self.sessions, self.last = [], None   # from /api/status: [{id, name, pid}], last target id
        self.pinned = tk.IntVar(value=0)      # 0 = follow focus
        self.target = None                    # session id chosen when recording started
        self.frames, self.stream, self.rec_start = [], None, 0.0
        self.toast_win = None
        self.press = None

        self.cv.bind("<ButtonPress-1>", self.on_press)
        self.cv.bind("<B1-Motion>", self.on_drag)
        self.cv.bind("<ButtonRelease-1>", self.on_release)
        self.cv.bind("<ButtonPress-3>", self.on_menu)
        self.menu = tk.Menu(root, tearoff=0)

        threading.Thread(target=self.poll_loop, daemon=True).start()
        self.tick()

    # ------------------------------------------------------------ position
    def load_pos(self):
        try:
            p = json.loads(POS_FILE.read_text(encoding="utf-8"))
            x, y = int(p["x"]), int(p["y"])
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            if 0 <= x <= sw - self.size and 0 <= y <= sh - self.size:
                return f"{x}+{y}"
        except Exception:
            pass
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        return f"{(sw - self.size) // 2}+{sh - self.size - round(96 * self.scale)}"

    def save_pos(self):
        try:
            POS_FILE.write_text(json.dumps({"x": self.root.winfo_x(), "y": self.root.winfo_y()}), encoding="utf-8")
        except OSError:
            pass

    # ------------------------------------------------------------ mouse
    def on_press(self, e):
        self.press = (e.x_root, e.y_root, self.root.winfo_x(), self.root.winfo_y(), False)

    def on_drag(self, e):
        if not self.press:
            return
        x0, y0, wx, wy, moved = self.press
        dx, dy = e.x_root - x0, e.y_root - y0
        if moved or abs(dx) + abs(dy) > 4:
            self.press = (x0, y0, wx, wy, True)
            self.root.geometry(f"+{wx + dx}+{wy + dy}")
            self.place_toast()

    def on_release(self, _e):
        if self.press and self.press[4]:
            self.save_pos()
        elif self.press:
            self.toggle()
        self.press = None

    def on_menu(self, e):
        m = self.menu
        m.delete(0, "end")
        m.add_radiobutton(label="跟随焦点", variable=self.pinned, value=0)
        names = [x["name"] for x in self.sessions]
        for x in self.sessions:
            label = x["name"] if names.count(x["name"]) == 1 else f'{x["name"]} #{x["id"]}'
            m.add_radiobutton(label=label, variable=self.pinned, value=x["id"])
        if not self.sessions:
            m.add_command(label="（没有会话在听）", state="disabled")
        m.add_separator()
        m.add_command(label="停止语音服务", command=self.stop_server)
        m.add_command(label="关闭悬浮球", command=self.root.destroy)
        # A popup menu only closes on an outside click if its owner is the foreground window.
        user32.SetForegroundWindow(self.hwnd)
        self.menu.tk_popup(e.x_root, e.y_root)

    # ------------------------------------------------------------ recording
    def choose_target(self):
        ids = [x["id"] for x in self.sessions]
        if self.pinned.get() in ids:
            return self.pinned.get()
        fg = user32.GetForegroundWindow()
        hits = []
        for x in self.sessions:
            try:
                if x.get("pid") and terminal_window(x["pid"]) == fg:
                    hits.append(x["id"])
            except Exception as ex:
                log("terminal_window:", ex)
        if not hits:
            return None   # voice.py falls back to the last target
        return self.last if self.last in hits else max(hits)

    def toggle(self):
        if self.state == "busy":
            return
        if self.stream:
            self.stop_rec()
            return
        self.frames = []
        try:
            self.stream = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16",
                                            callback=lambda data, *_: self.frames.append(bytes(data)))
            self.stream.start()
        except Exception as ex:
            self.stream = None
            self.fail(f"拿不到麦克风：{ex}")
            return
        self.rec_start = time.monotonic()
        self.state = "rec"
        self.target = self.choose_target()

    def stop_rec(self):
        self.stream.stop()
        self.stream.close()
        self.stream = None
        if time.monotonic() - self.rec_start < 0.4:
            self.state = "ready"
            self.toast("太短了")
            return
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(b"".join(self.frames))
        self.state = "busy"
        threading.Thread(target=self.send, args=(buf.getvalue(),), daemon=True).start()

    def send(self, wav):
        headers = {"X-Session": self.token, "Content-Type": "audio/wav"}
        if self.target:
            headers["X-Target"] = str(self.target)
        req = urllib.request.Request(self.base + "/api/audio", data=wav, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                name = json.loads(r.read()).get("target")
            self.root.after(0, self.sent, name)
        except urllib.error.HTTPError as ex:
            try:
                msg = json.loads(ex.read()).get("error") or str(ex)
            except Exception:
                msg = str(ex)
            self.root.after(0, self.fail, msg)
        except Exception as ex:
            self.root.after(0, self.fail, f"发送失败：{ex}")

    def sent(self, name):
        self.state = "ready"
        self.toast(f"→ {name}" if name else "已排队，等 Claude 连上", 1500)

    def fail(self, msg):
        log("error:", msg)
        self.state = "error"
        self.toast(msg, 4000)
        self.root.after(2500, lambda: self.state == "error" and setattr(self, "state", "ready"))

    # ------------------------------------------------------------ server
    def poll_loop(self):
        down_since = None
        while True:
            try:
                req = urllib.request.Request(self.base + "/api/status", headers={"X-Session": self.token})
                with urllib.request.urlopen(req, timeout=5) as r:
                    d = json.loads(r.read())
                down_since = None
                self.root.after(0, self.update_status, d)
            except Exception as ex:
                down_since = down_since or time.monotonic()
                if time.monotonic() - down_since > 15:
                    log("server gone:", ex)
                    self.root.after(0, self.root.destroy)
                    return
                try:   # the server may have restarted with a new token
                    self.base, self.token = session()
                except Exception:
                    pass
            time.sleep(2)

    def update_status(self, d):
        self.listening, self.sessions, self.last = d["listening"], d.get("sessions", []), d.get("last")
        if self.pinned.get() and self.pinned.get() not in [x["id"] for x in self.sessions]:
            self.pinned.set(0)   # the pinned session is gone: follow focus again

    def stop_server(self):
        try:
            urllib.request.urlopen(urllib.request.Request(self.base + "/api/stop", method="POST",
                                                          headers={"X-Session": self.token}), timeout=3)
        except Exception:
            pass
        self.root.destroy()

    # ------------------------------------------------------------ drawing
    def tick(self):
        if self.state in ("ready", "away"):
            self.state = "ready" if self.listening else "away"
        self.draw(self.state)
        self.root.after(80, self.tick)

    def draw(self, shown):
        cv, s, k = self.cv, self.size, self.scale
        cv.delete("all")
        pulse = (time.monotonic() * 2.2) % 2
        pulse = pulse if pulse < 1 else 2 - pulse      # 0..1..0
        pad = round(4 * k)
        if shown == "rec":
            ring = round(pad * pulse)
            cv.create_oval(pad - ring, pad - ring, s - pad + ring, s - pad + ring,
                           fill="", outline=COLORS[shown], width=max(1, round(2 * k)))
        cv.create_oval(pad, pad, s - pad, s - pad, fill=COLORS[shown], outline="")
        c = s / 2
        fg = "#ffffff"
        if shown == "rec":
            secs = int(time.monotonic() - self.rec_start)
            cv.create_text(c, c, text=str(secs), fill=fg, font=("Segoe UI", 13, "bold"))
        elif shown == "error":
            cv.create_text(c, c, text="!", fill=fg, font=("Segoe UI", 16, "bold"))
        elif shown == "busy":
            for i in range(3):
                on = int(time.monotonic() * 4) % 3 == i
                r = round((3 if on else 2) * k)
                x = c + (i - 1) * 9 * k
                cv.create_oval(x - r, c - r, x + r, c + r, fill=fg, outline="")
        else:   # mic icon
            w, lw = 5 * k, max(2, round(2 * k))
            cv.create_line(c, c - 9 * k, c, c - 1 * k, fill=fg, width=w * 2, capstyle="round")
            cv.create_arc(c - 8 * k, c - 10 * k, c + 8 * k, c + 6 * k, start=180, extent=180,
                          style="arc", outline=fg, width=lw)
            cv.create_line(c, c + 6 * k, c, c + 10 * k, fill=fg, width=lw)
        dot = round(5 * k)
        cv.create_oval(s - pad - 2 * dot, pad, s - pad, pad + 2 * dot,
                       fill="#22c55e" if self.listening else "#ef4444", outline=KEY, width=max(1, round(k)))

    # ------------------------------------------------------------ toast
    def toast(self, text, ms=2000):
        if self.toast_win:
            self.toast_win.destroy()
        t = self.toast_win = tk.Toplevel(self.root)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        tk.Label(t, text=text, bg="#18181b", fg="#fafafa", padx=10, pady=5, wraplength=round(260 * self.scale),
                 justify="left", font=("Microsoft YaHei UI", 9)).pack()
        no_activate(t)
        self.place_toast()
        t.after(ms, lambda: self.toast_win is t and (t.destroy(), setattr(self, "toast_win", None)))

    def place_toast(self):
        t = self.toast_win
        if not t:
            return
        t.update_idletasks()
        x = self.root.winfo_x() - t.winfo_width() - round(8 * self.scale)
        if x < 0:
            x = self.root.winfo_x() + self.size + round(8 * self.scale)
        y = self.root.winfo_y() + (self.size - t.winfo_height()) // 2
        t.geometry(f"+{x}+{y}")


def main():
    STATE.mkdir(parents=True, exist_ok=True)
    # One ball at a time.
    ctypes.windll.kernel32.CreateMutexW(None, False, "voice-chat-ball")
    if ctypes.windll.kernel32.GetLastError() == 183:   # ERROR_ALREADY_EXISTS
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    for _ in range(30):            # voice.py may still be starting
        if SESSION_FILE.exists():
            break
        time.sleep(0.5)
    else:
        log("no session file; is voice.py running?")
        return
    try:
        Ball().root.mainloop()
    except Exception as ex:
        log("crash:", repr(ex))
        raise


if __name__ == "__main__":
    main()
