"""Input sources. Each source pushes command strings onto a queue; the TV loop never cares where they came from.

Commands: up, down, left, right, last, info, guide, fullscreen, mute, volup, voldown, wheel_up, wheel_down,
          click, channel:<dial|id>, diag, help, captions, testcard, now, details, menu, sleep, bar, back, quit, enter, digit:<0-9>,
          text (teletext), page:<100-999>, red, green, yellow, blue (teletext colour keys),
          favourite, hide, favs_only (favourite / hide the current channel, only surf favourites),
          search:<words> (open the teletext search), char:<c> / backspace (only while the search page is open: see TEXT_MODE)

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
                "m": "mute", "[": "voldown", "]": "volup", "d": "diag", "?": "help", "h": "help", "c": "captions", "t": "testcard",
                "n": "now", "s": "menu", "z": "sleep", "x": "text", "r": "red", "y": "yellow", "b": "blue", "a": "favourite", "o": "hide", "e": "details", "WHEEL_UP": "wheel_up", "WHEEL_DOWN": "wheel_down",
                "NEXT": "up", "PREV": "down", "PLAYPAUSE": "mute",
                **{str(d): f"digit:{d}" for d in range(10)}}

TEXT_MODE = False        # True while the search page wants letters: keys then arrive as "char:<c>", "backspace" and "enter"
_EVDEV_CHARS = {2: "1", 3: "2", 4: "3", 5: "4", 6: "5", 7: "6", 8: "7", 9: "8", 10: "9", 11: "0",
                16: "q", 17: "w", 18: "e", 19: "r", 20: "t", 21: "y", 22: "u", 23: "i", 24: "o", 25: "p",
                30: "a", 31: "s", 32: "d", 33: "f", 34: "g", 35: "h", 36: "j", 37: "k", 38: "l",
                44: "z", 45: "x", 46: "c", 47: "v", 48: "b", 49: "n", 50: "m", 57: "space"}
_CHAR_KEYS = {"+": "up", "=": "up", "-": "down", "_": "down", "k": "up", "j": "down", "i": "info",
              "g": "guide", "l": "last", "q": "quit", "f": "fullscreen", "m": "mute", "[": "voldown", "]": "volup", "d": "diag", "?": "help", "h": "help", "c": "captions",
              "t": "testcard", "n": "now", "s": "menu", "z": "sleep", "x": "text", "r": "red", "y": "yellow", "b": "blue", "a": "favourite", "o": "hide", "e": "details", "\r": "enter", "\n": "enter",
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
            if TEXT_MODE and s and not s.startswith("\x1b"):
                ch = s[0]
                cmd = ("enter" if ch in "\r\n" else "backspace" if ch in "\x7f\b" else "char:space" if ch == " "
                       else "char:" + ch.lower() if ch.isalnum() else None)
            elif s.startswith("\x1b"):
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
    0x09: "guide", 0x0B: "now", 0x0A: "menu", 0x51: "captions",   # root menu, contents menu, setup menu, subtitle
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

    def _tell(self, text):
        try:
            if self._proc and self._proc.poll() is None:
                self._proc.stdin.write(text)
                self._proc.stdin.flush()
        except OSError:
            pass

    def standby(self):
        """Sleep timer ran out: put the TV into standby."""
        self._tell("standby 0\n")

    def wake(self):
        self._tell("on 0\nas\n")

    def stop(self):
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()


# ---- evdev: USB keyboards, remotes and IR receivers read straight from /dev/input (Linux) ----
# Works whatever terminal the client was started from (SSH, systemd), which the terminal input cannot do.
# The keyboard is grabbed while we run, so keys do not also type into the console (a login prompt on tty1).
KEY = {1: "back", 16: "quit", 28: "enter", 96: "enter", 103: "up", 108: "down", 105: "left", 106: "right",
       104: "up", 109: "down", 78: "up", 13: "up", 74: "down", 12: "down", 402: "up", 403: "down",
       26: "voldown", 27: "volup", 114: "voldown", 115: "volup", 113: "mute", 50: "mute",
       34: "guide", 23: "info", 358: "info", 365: "guide", 49: "now", 31: "menu", 139: "menu", 44: "sleep",
       46: "captions", 32: "diag", 20: "testcard", 38: "last", 35: "help", 53: "help", 33: "fullscreen",
       45: "text", 19: "red", 21: "yellow", 48: "blue", 30: "favourite", 24: "hide", 18: "details",
       2: "digit:1", 3: "digit:2", 4: "digit:3", 5: "digit:4", 6: "digit:5", 7: "digit:6", 8: "digit:7",
       9: "digit:8", 10: "digit:9", 11: "digit:0",
       79: "digit:1", 80: "digit:2", 81: "digit:3", 75: "digit:4", 76: "digit:5", 77: "digit:6",
       71: "digit:7", 72: "digit:8", 73: "digit:9", 82: "digit:0"}
_AUTOREPEAT_OK = {"volup", "voldown"}          # a held volume key keeps going; other keys act once per press


def find_keyboards(devices_text):
    """/dev/input/event numbers of everything that looks like a keyboard or key-sending remote, from /proc/bus/input/devices."""
    import re
    found = []
    for block in devices_text.split("\n\n"):
        m = re.search(r"Handlers=(.*)", block)
        if not m or "kbd" not in m.group(1):
            continue
        ev = re.search(r"event(\d+)", m.group(1))
        if ev:
            found.append(int(ev.group(1)))
    return found


def parse_events(buf, fmt="@llHHi"):
    """[(code, value)] for the key events (type 1) in a chunk read from an event device."""
    import struct
    size = struct.calcsize(fmt)
    out = []
    for i in range(0, len(buf) - size + 1, size):
        _s, _us, etype, code, value = struct.unpack_from(fmt, buf, i)
        if etype == 1:
            out.append((code, value))
    return out


class EvdevInput:
    def __init__(self, grab=True, devices_file="/proc/bus/input/devices"):
        self.grab, self.devices_file = grab, devices_file
        self._stop = threading.Event()
        self._fds = {}
        self._warned = False

    def start(self, put):
        if not sys.platform.startswith("linux") or not os.path.exists(self.devices_file):
            return
        threading.Thread(target=self._run, args=(put,), daemon=True, name="evdev").start()

    def _scan(self):
        try:
            with open(self.devices_file) as f:
                nums = find_keyboards(f.read())
        except OSError:
            return
        for n in nums:
            path = f"/dev/input/event{n}"
            if path in self._fds:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except PermissionError:
                if not self._warned:
                    self._warned = True
                    print(f"evdev: no permission for {path}: add the user to the 'input' group", file=sys.stderr)
                continue
            except OSError:
                continue
            if self.grab:
                try:
                    import fcntl
                    fcntl.ioctl(fd, 0x40044590, 1)           # EVIOCGRAB: we get the keys, the console does not
                except OSError:
                    pass
            self._fds[path] = fd

    def _run(self, put):
        import time
        last_scan = 0
        try:
            while not self._stop.is_set():
                if time.time() - last_scan > 5:                   # pick up keyboards plugged in later
                    self._scan()
                    last_scan = time.time()
                if not self._fds:
                    self._stop.wait(2)
                    continue
                try:
                    ready, _, _ = select.select(list(self._fds.values()), [], [], 1.0)
                except (OSError, ValueError):
                    continue
                for fd in ready:
                    try:
                        buf = os.read(fd, 24 * 64)
                    except BlockingIOError:
                        continue
                    except OSError:                               # unplugged
                        for p, f in list(self._fds.items()):
                            if f == fd:
                                del self._fds[p]
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                        continue
                    for code, value in parse_events(buf):
                        if TEXT_MODE and code in _EVDEV_CHARS:
                            cmd = "char:" + _EVDEV_CHARS[code]
                        elif TEXT_MODE and code == 14:
                            cmd = "backspace"
                        else:
                            cmd = KEY.get(code)
                        if cmd and (value == 1 or (value == 2 and cmd in _AUTOREPEAT_OK)):
                            put(cmd)
        finally:
            for fd in list(self._fds.values()):                   # closed here, by the thread that uses them
                try:
                    os.close(fd)
                except OSError:
                    pass
            self._fds.clear()

    def stop(self):
        self._stop.set()
