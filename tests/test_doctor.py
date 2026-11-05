import io
import os
import tempfile
import unittest

from support import make_tv  # noqa: F401  (puts the project on sys.path)
import doctor

MPV_HELP = """List of detected audio devices:
  'auto' (Autoselect device)
  'coreaudio/BuiltInSpeakerDevice' (MacBook Pro Speakers)
  'alsa/plughw:CARD=Headphones,DEV=0' (bcm2835 Headphones (Plug)))
"""


class Parsers(unittest.TestCase):
    def test_mpv_version(self):
        self.assertEqual(doctor.parse_mpv_version("mpv 0.37.0 Copyright (c) 2000-2023"), (0, 37))
        self.assertEqual(doctor.parse_mpv_version("mpv v0.38.0-123-gabc"), (0, 38))
        self.assertIsNone(doctor.parse_mpv_version("command not found"))

    def test_audio_devices(self):
        devs = doctor.parse_audio_devices(MPV_HELP)
        self.assertEqual([n for n, _ in devs], ["auto", "coreaudio/BuiltInSpeakerDevice", "alsa/plughw:CARD=Headphones,DEV=0"])
        self.assertEqual(devs[1][1], "MacBook Pro Speakers")

    def test_audio_device_arg_takes_the_last(self):
        self.assertEqual(doctor.audio_device_arg(["mpv", "--audio-device=a", "--x", "--audio-device=b"]), "b")
        self.assertIsNone(doctor.audio_device_arg(["mpv"]))

    def test_throttled(self):
        self.assertEqual(doctor.decode_throttled(0), [])
        self.assertEqual(doctor.decode_throttled(0x50005), ["under-voltage right now", "throttled right now",
                                                           "under-voltage has happened", "throttling has happened"])


class FakeEnv(doctor.Env):
    platform = "linux"

    def __init__(self, commands=None, files=None, have=(), readable=()):
        self.commands, self.files, self.have, self.readable = commands or {}, files or {}, set(have), set(readable)

    def run(self, argv, timeout=10):
        for a in argv[1:] + argv[:1]:                     # commands are keyed by one distinctive argument
            if a in self.commands:
                return self.commands[a]
        return None, "no such program"

    def which(self, name):
        return f"/usr/bin/{name}" if name in self.have else None

    def read(self, path):
        return self.files.get(path)

    def access(self, path, mode):
        return path in self.readable

    def exists(self, path):
        return path in self.readable

    def get(self, url, timeout=8):
        raise OSError("offline")


def cfg(tmp, **extra):
    return {"profile": "pi", "cache_dir": tmp, "prof": {"mpv": ["mpv", "--vo=gpu", "--gpu-context=drm"]}, **extra}


class Checks(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.tmp = self._d.name

    def tearDown(self):
        self._d.cleanup()

    def status(self, check, env, **extra):
        return [r[0] for r in check(cfg(self.tmp, **extra), env)]

    def test_missing_mpv_fails_with_the_install_command(self):
        r = doctor.check_mpv(cfg(self.tmp), FakeEnv())
        self.assertEqual(r[0][0], doctor.FAIL)
        self.assertIn("apt install mpv", r[0][3])

    def test_old_mpv_fails(self):
        env = FakeEnv({"--version": (0, "mpv 0.30.0")}, have=["mpv"])
        self.assertEqual(self.status(doctor.check_mpv, env), [doctor.FAIL])

    def test_audio_device_the_profile_wants_must_exist(self):
        env = FakeEnv({"--audio-device=help": (0, MPV_HELP)}, have=["mpv"])
        self.assertEqual(self.status(doctor.check_audio, env), [doctor.OK])
        c = cfg(self.tmp)
        c["prof"]["mpv"].append("--audio-device=alsa/hw:9")
        r = doctor.check_audio(c, env)
        self.assertEqual(r[0][0], doctor.FAIL)
        self.assertIn("coreaudio/BuiltInSpeakerDevice", r[0][3])

    def test_unreadable_keyboard_says_which_group(self):
        files = {"/proc/bus/input/devices": "N: Name=\"Kbd\"\nH: Handlers=sysrq kbd event3 leds\n"}
        r = doctor.check_inputs(cfg(self.tmp), FakeEnv(files=files))
        self.assertEqual(r[0][0], doctor.FAIL)
        self.assertIn("input", r[0][3])
        ok = doctor.check_inputs(cfg(self.tmp), FakeEnv(files=files, readable=["/dev/input/event3"]))
        self.assertEqual(ok[0][0], doctor.OK)

    def test_display_needs_dri_for_drm_profiles(self):
        self.assertEqual(self.status(doctor.check_display, FakeEnv()), [doctor.FAIL])
        self.assertEqual(self.status(doctor.check_display, FakeEnv(readable=["/dev/dri/card0"])), [doctor.OK])

    def test_enabled_cec_without_the_binary_fails(self):
        self.assertEqual(self.status(doctor.check_cec, FakeEnv(), cec={"enabled": True}), [doctor.FAIL])
        self.assertEqual(self.status(doctor.check_cec, FakeEnv()), [doctor.INFO])

    def test_pi_undervoltage_is_a_problem(self):
        env = FakeEnv({"get_throttled": (0, "throttled=0x50005"), "measure_temp": (0, "temp=81.0'C")}, have=["vcgencmd"])
        self.assertEqual(self.status(doctor.check_pi, env), [doctor.FAIL, doctor.WARN])

    def test_offline_network_fails_both_hosts(self):
        self.assertEqual(self.status(doctor.check_network, FakeEnv()), [doctor.FAIL, doctor.FAIL])

    def test_certificate_errors_get_their_own_advice(self):
        class Env(FakeEnv):
            def get(self, url, timeout=8):
                raise OSError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
        r = doctor.check_network(cfg(self.tmp), Env())
        self.assertIn("certificate", r[0][3].lower())

    def test_a_crashing_check_does_not_hide_the_rest(self):
        original = list(doctor.CHECKS)
        try:
            doctor.CHECKS[:] = [lambda c, e: 1 / 0, doctor.check_python]
            res = doctor.run_checks(cfg(self.tmp), FakeEnv())
        finally:
            doctor.CHECKS[:] = original
        self.assertEqual([r[0] for r in res], [doctor.WARN, doctor.OK])

    def test_report_and_exit_code(self):
        out = io.StringIO()
        env = FakeEnv()
        code = doctor.run(cfg(self.tmp), env, out)
        self.assertEqual(code, 1)
        self.assertIn("[FAIL] mpv", out.getvalue())
        self.assertIn("problems", out.getvalue())


class AudioMenuTests(unittest.TestCase):
    def test_menu_cycles_devices_and_remembers_the_choice(self):
        with tempfile.TemporaryDirectory() as d:
            tv = make_tv(d)
            sent = []
            tv.mpv.command = lambda *a: sent.append(a)
            tv.audio = {"list": [("a", "Speakers"), ("b", "HDMI")], "cur": "a"}
            entry = lambda: next(e for e in tv.menu_entries() if e[0] == "Audio output")
            self.assertEqual(entry()[1], "Speakers")
            entry()[2](1)
            self.assertEqual(entry()[1], "HDMI")
            self.assertIn(("set_property", "audio-device", "b"), sent)
            self.assertEqual(tv.store.load()["settings"]["audio_device"], "b")
            entry()[2](1)
            self.assertEqual(entry()[1], "Speakers")                       # wraps

    def test_no_devices_is_harmless(self):
        with tempfile.TemporaryDirectory() as d:
            tv = make_tv(d)
            entry = next(e for e in tv.menu_entries() if e[0] == "Audio output")
            self.assertEqual(entry[1], "not available")
            entry[2](1)


if __name__ == "__main__":
    unittest.main()
