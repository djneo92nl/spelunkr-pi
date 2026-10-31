"""Input sources. Each source pushes command strings onto a queue; the TV loop never cares where they came from.

Commands: up, down, left, right, last, info, guide, fullscreen, mute, volup, voldown, wheel_up, wheel_down,
          click, channel:<dial|id>, quit, enter, digit:<0-9>

To add GPIO buttons or a USB remote later, write another class with start(put) / stop() and add it in tv.py:

    class GpioInput:
        def start(self, put): ...   # e.g. gpiozero Button(17).when_pressed = lambda: put("up")
        def stop(self): ...

mpv's own window (Mac, or HDMI with a focused window) also sends keys; see KEY_BINDINGS below.
"""
import atexit
import os
import select
import shlex
import sys
import threading

# mpv key name -> command. Used for keys pressed in mpv's own window.
KEY_BINDINGS = {"UP": "up", "DOWN": "down", "PGUP": "up", "PGDWN": "down", "+": "up", "-": "down",
                "LEFT": "left", "RIGHT": "right", "ENTER": "enter", "i": "info", "g": "guide", "l": "last", "q": "quit", "f": "fullscreen", "MBTN_LEFT_DBL": "fullscreen", "MBTN_LEFT": "click",
                "m": "mute", "[": "voldown", "]": "volup", "WHEEL_UP": "wheel_up", "WHEEL_DOWN": "wheel_down",
                "NEXT": "up", "PREV": "down", "PLAYPAUSE": "mute",
                **{str(d): f"digit:{d}" for d in range(10)}}

_CHAR_KEYS = {"+": "up", "=": "up", "-": "down", "_": "down", "k": "up", "j": "down", "i": "info",
              "g": "guide", "l": "last", "q": "quit", "f": "fullscreen", "m": "mute", "[": "voldown", "]": "volup", "\r": "enter", "\n": "enter",
              **{str(d): f"digit:{d}" for d in range(10)}}
_ESC_KEYS = {"[A": "up", "[B": "down", "[C": "right", "[D": "left", "OC": "right", "OD": "left", "[5~": "up", "[6~": "down", "OA": "up", "OB": "down"}


class KeyboardInput:
    """Reads single keypresses from the terminal (works over SSH). Does nothing if stdin is not a terminal."""

    def __init__(self):
        self._stop = threading.Event()
        self._saved = None

    def start(self, put):
        if not sys.stdin.isatty():
            return
        import termios
        import tty
        fd = sys.stdin.fileno()
        self._saved = (fd, termios.tcgetattr(fd))
        atexit.register(self.stop)
        tty.setcbreak(fd)
        threading.Thread(target=self._loop, args=(fd, put), daemon=True).start()

    def _loop(self, fd, put):
        while not self._stop.is_set():
            if not select.select([fd], [], [], 0.2)[0]:
                continue
            data = os.read(fd, 8)
            if not data:
                return
            s = data.decode("ascii", "ignore")
            if s.startswith("\x1b"):
                cmd = _ESC_KEYS.get(s[1:])
            else:
                cmd = _CHAR_KEYS.get(s[:1])
            if cmd:
                put(cmd)

    def stop(self):
        self._stop.set()
        if self._saved:
            import termios
            fd, attrs = self._saved
            self._saved = None
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, attrs)
            except termios.error:
                pass


# ---- HDMI-CEC (TV remote over the HDMI cable) ----
# libcec user-control codes (hex) -> our commands. The code is what cec-client prints in "key pressed: <name> (<code>)".
CEC_KEYS = {
    0x01: "up", 0x02: "down", 0x03: "left", 0x04: "right",      # d-pad
    0x00: "enter", 0x2B: "enter",                               # select, enter
    0x0D: "back",                                               # exit
    0x30: "up", 0x31: "down", 0x32: "last",                     # channel up / down, previous channel
    0x37: "up", 0x38: "down",                                   # page up / down
    0x35: "info", 0x53: "guide",                                # display information, EPG
    0x09: "guide", 0x0B: "guide",                               # root menu, contents menu
    0x41: "volup", 0x42: "voldown", 0x43: "mute",               # volume (usually handled by the TV itself)
    0x72: "last", 0x73: "guide", 0x74: "info", 0x71: "mute",    # red, green, yellow, blue
    **{0x20 + d: f"digit:{d}" for d in range(10)},              # 0-9
}
_REPEATING = {"up", "down", "last"}                             # held keys repeat: act at most every 0.3 s


def parse_cec_line(line):
    """'DEBUG: ... key pressed: channel up (30)' -> 'up'; anything else -> None."""
    i = line.find("key pressed:")
    if i < 0:
        return None
    rest = line[i + len("key pressed:"):].strip()
    j = rest.rfind("(")
    if j < 0 or not rest.endswith(")"):
        return None
    try:
        return CEC_KEYS.get(int(rest[j + 1:-1], 16))
    except ValueError:
        return None


class CecInput:
    """Runs `cec-client` and turns TV-remote key presses into commands. Needs the `cec-utils` package and an HDMI port with CEC.
    Does nothing (with one message) if cec-client is not installed. Restarts cec-client if it exits."""

    def __init__(self, name="Spelunkr TV", wake_tv=True, binary="cec-client", log_level=16):
        self.name, self.wake_tv, self.binary, self.log_level = name, wake_tv, binary, log_level
        self._stop = threading.Event()
        self._proc = None
        self._last = {}

    def start(self, put):
        threading.Thread(target=self._run, args=(put,), daemon=True, name="cec").start()

    def _run(self, put):
        import subprocess
        import time
        while not self._stop.is_set():
            try:
                self._proc = subprocess.Popen(
                    shlex.split(self.binary) + ["-t", "p", "-o", self.name, "-d", str(self.log_level)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
            except FileNotFoundError:
                print(f"CEC: '{self.binary}' not found (install the cec-utils package); CEC disabled", file=sys.stderr)
                return
            if self.wake_tv:
                try:
                    self._proc.stdin.write("on 0\nas\n")        # power the TV on and make us the active source
                    self._proc.stdin.flush()
                except OSError:
                    pass
            for line in self._proc.stdout:
                cmd = parse_cec_line(line)
                if cmd:
                    now = time.time()
                    if cmd in _REPEATING and now - self._last.get(cmd, 0) < 0.3:
                        continue
                    self._last[cmd] = now
                    put(cmd)
            if self._stop.wait(10):                              # cec-client ended (adapter busy / unplugged): retry
                return

    def stop(self):
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
