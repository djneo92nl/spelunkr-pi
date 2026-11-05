"""`python3 tv.py --doctor`: checks that this machine can run the client and says how to fix what it cannot.

Nothing here plays video or needs a display, so it is safe to run over SSH on a fresh Pi. Every check returns
(status, title, detail, fix) with status "ok", "warn", "fail" or "info"; the exit code is 1 if anything failed.
"""
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

import inputs
import schedule as S

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"


# ---- small parsers (tested without hardware) ----
def parse_mpv_version(text):
    """(major, minor) from 'mpv 0.37.0 Copyright ...' or 'mpv v0.38.0-...'; None if it does not look like mpv."""
    m = re.search(r"mpv\s+v?(\d+)\.(\d+)", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_audio_devices(text):
    """[(name, description)] from `mpv --audio-device=help`: lines like `  'alsa/default' (Default (alsa))`."""
    out = []
    for line in (text or "").splitlines():
        m = re.match(r"\s*'([^']+)'\s*\((.*)\)\s*$", line)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def audio_device_arg(mpv_args):
    """The --audio-device the profile asks for, or None."""
    found = None
    for a in mpv_args:
        if a.startswith("--audio-device="):
            found = a.split("=", 1)[1]
    return found


_THROTTLE_BITS = [(0, "under-voltage right now"), (1, "ARM frequency capped"), (2, "throttled right now"),
                  (3, "soft temperature limit"), (16, "under-voltage has happened"), (17, "frequency cap has happened"),
                  (18, "throttling has happened"), (19, "soft temperature limit has happened")]


def decode_throttled(value):
    """Messages for the set bits of `vcgencmd get_throttled`."""
    return [msg for bit, msg in _THROTTLE_BITS if value & (1 << bit)]


# ---- environment, injectable for tests ----
class Env:
    def run(self, argv, timeout=10):
        """(returncode, stdout+stderr) or (None, reason) when the program cannot be run."""
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            return p.returncode, (p.stdout or "") + (p.stderr or "")
        except (OSError, subprocess.SubprocessError) as ex:
            return None, str(ex)

    def which(self, name):
        return shutil.which(name)

    def get(self, url, timeout=8):
        """(status, headers, seconds, body_start) or raises."""
        req = urllib.request.Request(url, headers={"User-Agent": S.UA})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers, time.time() - t0, r.read(4096)

    platform = sys.platform
    access = staticmethod(os.access)
    exists = staticmethod(os.path.exists)

    def read(self, path):
        try:
            with open(path) as f:
                return f.read()
        except OSError:
            return None


# ---- the checks ----
def check_python(cfg, env):
    v = sys.version_info
    if v < (3, 9):
        return [(FAIL, "Python", f"{v.major}.{v.minor} is too old", "install Python 3.9 or newer")]
    return [(OK, "Python", f"{v.major}.{v.minor}.{v.micro}", "")]


def check_mpv(cfg, env):
    binary = cfg["prof"]["mpv"][0]
    path = env.which(binary)
    if not path:
        fix = "sudo apt install mpv" if env.platform.startswith("linux") else "brew install mpv"
        return [(FAIL, "mpv", f"'{binary}' is not installed", fix)]
    code, out = env.run([path, "--version"])
    ver = parse_mpv_version(out)
    if ver is None:
        return [(WARN, "mpv", f"{path} did not report a version", "check that it runs: mpv --version")]
    if ver < (0, 33):
        return [(FAIL, "mpv", f"version {ver[0]}.{ver[1]} is too old for the on-screen graphics", "install mpv 0.33 or newer")]
    return [(OK, "mpv", f"{ver[0]}.{ver[1]} at {path}", "")]


def check_cache(cfg, env):
    d = cfg["cache_dir"]
    try:
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, ".doctor")
        with open(probe, "w") as f:
            f.write("x")
        os.remove(probe)
    except OSError as ex:
        return [(FAIL, "cache folder", f"{d}: {ex}", "pick a writable folder with \"cache_dir\" in config.json")]
    return [(OK, "cache folder", d, "")]


def check_network(cfg, env):
    out = []
    for label, url in (("data.spelunkr.tv", S.DATA + "lineup.json"), ("archive.org", "https://archive.org/")):
        try:
            status, headers, secs, _ = env.get(url)
            detail = f"HTTP {status} in {secs:.1f}s"
            if label.startswith("data") and headers.get("Date"):
                from email.utils import parsedate_to_datetime
                skew = parsedate_to_datetime(headers["Date"]).timestamp() - time.time()
                if abs(skew) > 2:
                    out.append((WARN, "clock", f"this clock is {-skew:+.0f}s off; the client corrects for it",
                                "enable NTP: sudo timedatectl set-ntp true"))
            out.append((OK if secs < 4 else WARN, label, detail, "" if secs < 4 else "slow link: expect long start-up times"))
        except Exception as ex:
            if "CERTIFICATE_VERIFY_FAILED" in str(ex):
                fix = ("Python cannot check HTTPS certificates: on a Mac run the 'Install Certificates.command' that comes with "
                       "Python (or use the python.org / Homebrew Python); on Linux: sudo apt install ca-certificates. "
                       "A proxy that re-signs HTTPS needs its own certificate installed too.")
                out.append((FAIL, label, "HTTPS certificate check failed (the client then runs from its cache only)", fix))
            else:
                out.append((FAIL, label, f"cannot reach it: {ex}", "check the network, DNS and the date and time"))
    return out


def check_audio(cfg, env):
    path = env.which(cfg["prof"]["mpv"][0])
    if not path:
        return []
    code, text = env.run([path, "--no-config", "--audio-device=help"])
    devices = parse_audio_devices(text)
    want = audio_device_arg(cfg["prof"]["mpv"])
    if not devices:
        return [(WARN, "audio devices", "mpv lists none", "check that a sound card is present: aplay -l")]
    names = ", ".join(n for n, _ in devices[:6]) + (" ..." if len(devices) > 6 else "")
    if want and want not in {n for n, _ in devices}:
        return [(FAIL, "audio device", f"profile '{cfg['profile']}' wants {want}, which mpv does not list",
                 f"pick one from: {names}  (settings menu, or --audio-device in config.json)")]
    return [(OK, "audio devices", f"{len(devices)} found" + (f"; profile uses {want}" if want else "; profile uses the default"), "")]


def check_inputs(cfg, env):
    if not env.platform.startswith("linux"):
        return [(INFO, "keyboard", "terminal and mpv-window keys only on this platform", "")]
    if not cfg.get("evdev", True):
        return [(INFO, "keyboard", "evdev input is switched off in config.json", "")]
    text = env.read("/proc/bus/input/devices")
    nums = inputs.find_keyboards(text) if text else []
    if not nums:
        return [(WARN, "keyboard", "no USB keyboard or remote found", "plug one in; terminal keys still work over SSH")]
    bad = [n for n in nums if not env.access(f"/dev/input/event{n}", os.R_OK)]
    if bad:
        return [(FAIL, "keyboard", f"cannot read /dev/input/event{bad[0]}", "sudo usermod -aG input $USER, then log in again")]
    return [(OK, "keyboard", f"{len(nums)} input device(s) readable", "")]


def check_display(cfg, env):
    args = cfg["prof"]["mpv"]
    if not any(a in ("--vo=drm", "--gpu-context=drm") for a in args):
        return []
    if not env.exists("/dev/dri/card0"):
        return [(FAIL, "display", "no /dev/dri/card0", "enable the KMS driver (dtoverlay=vc4-kms-v3d on a Pi) and reboot")]
    if not env.access("/dev/dri/card0", os.R_OK | os.W_OK):
        return [(FAIL, "display", "cannot open /dev/dri/card0", "sudo usermod -aG video,render $USER, then log in again")]
    return [(OK, "display", "/dev/dri/card0 is usable", "")]


def check_cec(cfg, env):
    cec = cfg.get("cec") or {}
    have = env.which(cec.get("binary", "cec-client"))
    if cec.get("enabled") and not have:
        return [(FAIL, "HDMI-CEC", "enabled, but cec-client is not installed", "sudo apt install cec-utils")]
    if cec.get("enabled"):
        return [(OK, "HDMI-CEC", "cec-client found", "")]
    return [(INFO, "HDMI-CEC", "off" + (" (cec-client is installed: --cec turns it on)" if have else ""), "")]


def check_pi(cfg, env):
    if not env.which("vcgencmd"):
        return []
    out = []
    code, text = env.run(["vcgencmd", "get_throttled"])
    m = re.search(r"0x([0-9a-fA-F]+)", text or "")
    if m:
        flags = decode_throttled(int(m.group(1), 16))
        out.append((FAIL if any("under-voltage right now" in f for f in flags) else WARN if flags else OK, "Pi power and heat",
                    "; ".join(flags) or "no under-voltage or throttling", "use a better power supply and cable" if flags else ""))
    code, text = env.run(["vcgencmd", "measure_temp"])
    m = re.search(r"([\d.]+)", text or "")
    if m:
        t = float(m.group(1))
        out.append((WARN if t >= 75 else OK, "Pi temperature", f"{t:.0f} C", "add a heatsink or some airflow" if t >= 75 else ""))
    return out


def check_fonts(cfg, env):
    import teletext
    font = teletext._font
    if env.platform == "darwin" or not env.which("fc-list"):
        return [(INFO, "teletext font", font, "")]
    code, text = env.run(["fc-list", font])
    if font.lower() in (text or "").lower():
        return [(OK, "teletext font", font, "")]
    return [(WARN, "teletext font", f"{font} is not installed, so teletext columns may not line up",
             "sudo apt install fonts-dejavu-core   (or set \"teletext_font\" in config.json)")]


def check_pixel_fonts(cfg, env):
    import osd
    fonts = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
    if cfg.get("pixel_fonts", True) is False:
        return [(INFO, "overlay fonts", "pixel fonts are switched off in config.json", "")]
    if not (env.exists(fonts) and any(f.endswith(".ttf") for f in os.listdir(fonts))):
        return [(WARN, "overlay fonts", "the fonts/ folder is missing, so the overlays use the plain look", "restore fonts/ from the project (see fonts/README.md)")]
    path = env.which(cfg["prof"]["mpv"][0])
    code, text = env.run([path, "--no-config", "--list-options"]) if path else (None, "")
    if "--osd-fonts-dir" in (text or ""):
        return [(OK, "overlay fonts", "mpv loads the website's pixel fonts from fonts/", "")]
    if env.which("fc-list") and "star4" in (env.run(["fc-list", "Star4 Large"])[1] or "").lower():
        return [(OK, "overlay fonts", "the pixel fonts are installed system-wide", "")]
    return [(WARN, "overlay fonts", "this mpv cannot load fonts from a folder and the fonts are not installed, so the overlays use the plain look",
             "sudo mkdir -p /usr/local/share/fonts/spelunkr && sudo cp fonts/*.ttf /usr/local/share/fonts/spelunkr/ && fc-cache -f   (pi/setup.sh does this)")]


def check_feeds(cfg, env):
    out = []
    for f in cfg.get("feeds") or []:
        if not isinstance(f, dict) or not f.get("url"):
            out.append((WARN, "news feed", f"entry without a url: {f!r}", "each feed needs {\"name\": ..., \"url\": ...}"))
            continue
        try:
            import feeds
            n = len(feeds.fetch(f["url"], cfg["cache_dir"], max_age=0, timeout=8))
            out.append((OK if n else WARN, f"feed {f.get('name') or f['url']}", f"{n} stories", "" if n else "the feed is empty"))
        except Exception as ex:
            out.append((FAIL, f"feed {f.get('name') or f['url']}", str(ex)[:80], "check the address; it must be an RSS or Atom feed"))
    return out


CHECKS = [check_python, check_mpv, check_cache, check_network, check_audio, check_inputs, check_display, check_cec,
          check_pi, check_fonts, check_pixel_fonts, check_feeds]


def run_checks(cfg, env=None):
    env = env or Env()
    results = []
    for check in CHECKS:
        try:
            results += check(cfg, env)
        except Exception as ex:                      # a broken check must not hide the others
            results.append((WARN, check.__name__.replace("check_", ""), f"could not run: {type(ex).__name__}: {ex}", ""))
    return results


def run(cfg, env=None, out=sys.stdout):
    """Print the report; return the exit code (1 if any check failed)."""
    tty = hasattr(out, "isatty") and out.isatty()
    col = {OK: "32", WARN: "33", FAIL: "31", INFO: "36"}
    tag = {OK: " OK ", WARN: "WARN", FAIL: "FAIL", INFO: "INFO"}
    results = run_checks(cfg, env)
    print(f"spelunkr.tv doctor   profile: {cfg['profile']}\n", file=out)
    for status, title, detail, fix in results:
        t = f"\x1b[{col[status]}m{tag[status]}\x1b[0m" if tty else tag[status]
        print(f"[{t}] {title}: {detail}", file=out)
        if fix:
            print(f"       -> {fix}", file=out)
    n = {s: sum(1 for r in results if r[0] == s) for s in (OK, WARN, FAIL)}
    print(f"\n{n[OK]} ok, {n[WARN]} warnings, {n[FAIL]} problems", file=out)
    return 1 if n[FAIL] else 0
