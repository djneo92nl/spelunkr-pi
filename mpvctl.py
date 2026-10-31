"""Minimal mpv JSON-IPC controller: one long-lived mpv in idle mode, driven by commands.

Keeping a single mpv process (instead of launching one per item) avoids the start-up cost on a Pi 1B.
Events from mpv (end-file, client-message ...) are pushed onto `events` as dicts.
"""
import itertools
import json
import os
import queue
import re
import socket
import subprocess
import tempfile
import threading
import time


_ASS_COLOUR = re.compile(r"&H([0-9A-Fa-f]{6})&")        # &HBBGGRR& (alpha codes have only two digits, so they are left alone)


def _to_gray(match):
    h = match.group(1)
    b, g, r = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    v = int(255 * lum ** 0.75)                              # a little gamma so dark UI colours stay apart on a gray screen
    return f"&H{v:02X}{v:02X}{v:02X}&"


class Mpv:
    mono = False           # True: turn every overlay colour into its gray equivalent (black-and-white screens)

    def __init__(self, argv_template, key_bindings=None, mono=False):
        """argv_template: list with {sock} and {inputconf} placeholders. key_bindings: {mpv-key: tv-command}."""
        self.mono = mono
        self.dir = tempfile.mkdtemp(prefix="spelunkr-")
        self.sock_path = os.path.join(self.dir, "mpv.sock")
        conf = os.path.join(self.dir, "input.conf")
        with open(conf, "w") as f:
            for key, cmd in (key_bindings or {}).items():
                f.write(f"{key} script-message tv {cmd}\n")
        argv = [a.replace("{sock}", self.sock_path).replace("{inputconf}", conf) for a in argv_template]
        self.proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        self.events = queue.Queue()
        self._ids = itertools.count(1)
        self._waiting = {}          # request_id -> (Event, [response])
        self._lock = threading.Lock()
        self._sock = self._connect()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    # -- connection --
    def _connect(self, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            if self.proc.poll() is not None:
                raise RuntimeError(f"mpv exited with code {self.proc.returncode} at start-up")
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(self.sock_path)
                return s
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("could not connect to mpv IPC socket")

    def _read_loop(self):
        buf = b""
        while True:
            try:
                chunk = self._sock.recv(65536)
            except OSError:
                chunk = b""
            if not chunk:
                self.events.put({"event": "mpv-exited"})
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                rid = msg.get("request_id")
                if rid is not None:
                    with self._lock:
                        w = self._waiting.pop(rid, None)
                    if w:
                        w[1].append(msg)
                        w[0].set()
                elif "event" in msg:
                    self.events.put(msg)

    # -- commands --
    def _send(self, payload):
        data = (json.dumps(payload) + "\n").encode()
        with self._lock:
            self._sock.sendall(data)

    def command(self, *args):
        """Fire and forget."""
        self._send({"command": list(args)})

    def request(self, *args, timeout=3):
        """Send a command and wait for its reply; returns the 'data' field or None."""
        rid = next(self._ids)
        ev, box = threading.Event(), []
        with self._lock:
            self._waiting[rid] = (ev, box)
        self._send({"command": list(args), "request_id": rid})
        if not ev.wait(timeout):
            with self._lock:
                self._waiting.pop(rid, None)
            return None
        r = box[0]
        return r.get("data") if r.get("error") == "success" else None

    def get(self, prop, timeout=3):
        return self.request("get_property", prop, timeout=timeout)

    # -- helpers used by the TV client --
    def play(self, url, start=0.0, length=None):
        opts = [f"start={start:.3f}"]
        if length is not None:
            opts.append(f"length={length:.3f}")
        if self._new_loadfile():
            self.command("loadfile", url, "replace", -1, ",".join(opts))
        else:
            self.command("loadfile", url, "replace", ",".join(opts))

    _loadfile_has_index = None

    def _new_loadfile(self):
        # mpv >= 0.38 takes (url, flags, index, options); older takes (url, flags, options).
        if Mpv._loadfile_has_index is None:
            v = self.get("mpv-version") or "mpv 0.0"
            try:
                parts = v.split()[1].lstrip("v").split("-")[0].split(".")
                Mpv._loadfile_has_index = (int(parts[0]), int(parts[1])) >= (0, 38)
            except (IndexError, ValueError):
                Mpv._loadfile_has_index = True
        return Mpv._loadfile_has_index

    def stop(self):
        self.command("stop")

    def seek_abs(self, seconds):
        self.command("seek", f"{seconds:.3f}", "absolute+exact")

    res = (768, 576)       # ASS canvas; the TV loop updates it when the window shape changes

    def overlay(self, oid, ass):
        """Draw (or clear, if ass is empty) an ASS overlay layer."""
        if ass:
            if self.mono:
                ass = _ASS_COLOUR.sub(_to_gray, ass)
            self.command("osd-overlay", oid, "ass-events", ass, self.res[0], self.res[1])
        else:
            self.command("osd-overlay", oid, "none", "")

    def quit(self):
        try:
            self.command("quit")
        except OSError:
            pass
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
