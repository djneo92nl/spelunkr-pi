# Installing on a Raspberry Pi 1B (PAL composite)

From a blank SD card to a TV channel on your goggles. Takes about 30 minutes plus download time.

> **Status:** the client is tested on a Mac. This guide has **not** been run on a Pi yet. Steps marked **(unverified)** are the
> ones where I expect to need a correction once you have tried them. If something differs, note it down and send it to me.

## Which Pi

Written for a **Pi 1 Model B** (the weakest target), but it works for a **Pi 2** or **Pi 3** too, and those are the easier boards: more CPU and
1 GB of RAM, so the performance worries below mostly disappear.

**Memory matters more than the CPU:**

* **A 256 MB Pi 1 (Model B Rev 1, `grep Revision /proc/cpuinfo` shows `0002`/`0003`/`0004`-`0006`) is not usable with this software.**
  The firmware refuses to apply the KMS display overlay on boards with less than 512 MB (`dterror: Overlay 'vc4-kms-v3d' needs at least 512MB`
  in `sudo vclog -m`), so there is no `/dev/dri` and mpv cannot show video. Loading the overlay by hand after boot works, but with ~170 MB usable
  everything swaps to the SD card (measured: building one schedule took 94 s, 20 s of decoding took 5 minutes).
* **A 512 MB Pi 1 (Revision `000d`/`000e`/`000f`) is the smallest worthwhile board.** Not tested yet.
* A Pi 2 / 3 (1 GB) is the comfortable choice.

Differences to know about:

* **Pi 2 / 3 composite uses the 4-pole 3.5 mm "AV" jack, not a yellow RCA socket.** You need a cable made for it, with the Raspberry Pi pin-out:
  tip = left audio, first ring = right audio, second ring = ground, **sleeve = video**. Many camcorder cables have video and ground swapped;
  if you get no picture or a rolling one, try a different cable. This jack carries both video and audio.
* Pi 2 and Pi 1 have no Wi-Fi built in (a Pi 3 does). Use Ethernet for the install, or a USB Wi-Fi dongle.
* In Raspberry Pi Imager pick the device you actually have (Raspberry Pi 2 or 3). A Pi 2 / 3 runs the same 32-bit Lite image fine.
* Everything else, the probe and the profiles, is identical. A Pi 2 can also run `--hwdec` options the Pi 1 may not,
  so run `pi/probe.sh` and use what it shows.

## What you need

* Raspberry Pi 1 Model B (or a Pi 2 / 3, see above), 5 V power supply (1 A or more; 2 A is safer for a Pi 2 / 3).
* SD card, 8 GB or larger.
* **Network.** The Pi 1B has Ethernet only, so use a cable for the first install (a USB Wi-Fi dongle can come later).
* Your goggles' AV cable into the Pi's composite output: the **yellow RCA socket** on a Pi 1B (audio from its separate 3.5 mm jack),
  or the **4-pole 3.5 mm AV jack** on a Pi 2 / 3 (video and audio together).
* A USB keyboard (for changing channel and for the first tests). Keep HDMI **unplugged** while you set up composite (see step 6).
* Your Mac, to flash the card and SSH in.

## 1. Flash the SD card

1. Install **Raspberry Pi Imager** on the Mac (<https://www.raspberrypi.com/software/>).
2. Choose your device (**Raspberry Pi 1**, 2 or 3), OS **Raspberry Pi OS (other) → Raspberry Pi OS Lite (32-bit)**.
   Use the image Imager offers for the Pi 1. Do not pick a 64-bit image: the Pi 1 cannot run it. **(unverified: which release, Bookworm or newer,
   works best on a Pi 1B. If the Lite image does not boot or `mpv` is missing, try Bookworm.)**
3. When Imager asks to customise settings (the gear / "Edit settings"), set:
   * hostname: `spelunkr`
   * **username `pi`** and a password. The service file expects the user `pi`. If you choose another name, edit `User=` and `Group=` in `pi/spelunkr-tv.service` and the `chown` line in `pi/setup.sh`.
   * enable **SSH** (password or your public key)
   * your **time zone** and keyboard layout (for example `Europe/Amsterdam`). The schedule depends on the clock being right.
4. Write the card, put it in the Pi, connect Ethernet and power.

## 2. Connect

Wait a minute or two, then from the Mac:

```bash
ssh pi@spelunkr.local
```

If `spelunkr.local` does not resolve, find the Pi's IP address in your router and use `ssh pi@<ip>`.

Check that the clock is right (the Pi has no battery clock, so it sets itself from the network):

```bash
date
timedatectl | grep -i synchronized      # should say: System clock synchronized: yes
```

## 3. Copy the project to the Pi

On the **Mac** (not on the Pi):

```bash
rsync -av --exclude __pycache__ "/Users/remko/Code/TV Tuner/" pi@spelunkr.local:~/spelunkr-tv/
```

## 4. Install

On the **Pi**:

```bash
cd ~/spelunkr-tv
sudo sh pi/setup.sh
```

This installs `mpv` and `python3`, copies the client to `/opt/spelunkr-tv`, and installs the systemd service **without starting it**.
It does not touch the boot config unless you add `--apply-boot-config`; this guide does it by hand in step 6.

Warm up the cache and check that the network side works:

```bash
python3 tv.py --list                     # lists 32 channels
python3 schedule.py computer-chronicles  # what is on now, and the offset
```

Compare that last output with <https://spelunkr.tv/computer-chronicles> on your phone. They should agree within a second or two.

## 5. Find out what the Pi can decode

```bash
sh pi/probe.sh
```

It prints the board and `mpv` version, then plays the same 15 seconds with several video-output and hardware-decoder combinations
and shows which ones report `Using hardware decoding` and how many frames were dropped. **(unverified: the probe has never been run on a Pi.)**

* Pick the combination with hardware decoding and the fewest dropped frames.
* Put its flags into `profiles.pi.mpv` in `/opt/spelunkr-tv/config.json` (`--vo=...`, `--hwdec=...`).
  Edit with `sudo nano /opt/spelunkr-tv/config.json`.
* If none of them decode in hardware, software decoding of low-bitrate 4:3 video may still be acceptable on a Pi 1B.
  Try `--hwdec=no` with `--vo=drm`, and see the troubleshooting section.

Please keep the probe's output and send it to me.

## 6. Composite PAL output

Composite only comes out if the Pi is not using HDMI. Unplug HDMI before booting for this step.

Edit the boot config:

```bash
sudo nano /boot/firmware/config.txt       # older images: /boot/config.txt
```

**Legacy firmware video path** (add at the end):

```
sdtv_mode=2          # PAL
sdtv_aspect=1        # 4:3
disable_overscan=1
enable_tvout=1       # harmless where it is not needed
```

**If your image uses the KMS driver** (the file contains `dtoverlay=vc4-kms-v3d`; `--vo=drm` needs this), change that line to:

```
dtoverlay=vc4-kms-v3d,composite
```

and add this **to the end of the single line** in `/boot/firmware/cmdline.txt` (do not add a new line):

```
video=Composite-1:720x576@50ie
```

**(unverified: which of the two applies depends on the image, and I have not tried either on a Pi 1B.)**
If the picture is black and white or rolls, the TV norm is probably wrong (NTSC instead of PAL).

Reboot:

```bash
sudo reboot
```

## 6a. Black-and-white screens

For a monochrome display use the `pi-bw` profile (grayscale video and gray overlays) everywhere this guide says `--profile pi`, and add
`sdtv_disable_colourburst=1` to `config.txt` (with the other composite lines) for a sharper picture. **(unverified on a Pi: if the video is
still coloured, add `--vf=lavfi=[hue=s=0]` to the profile; it costs CPU.)**

## 6b. Alternative: HDMI instead of composite

HDMI is the easy way to test the Pi (or to use a normal TV) and needs **no composite settings**: skip step 6, leave the `config.txt`
as the image made it, and plug HDMI in before booting. Then use the `hdmi` profile everywhere this guide says `--profile pi`:

```bash
python3 tv.py --profile hdmi
```

and in the service, change `--profile pi` to `--profile hdmi` in `/etc/systemd/system/spelunkr-tv.service`
(`sudo systemctl daemon-reload` afterwards). `mpv --vo=drm` fills the screen by itself. The overlays follow the screen shape on their own,
so a 16:9 TV gets 16:9 layouts (the 4:3 shows are pillar-boxed with black sides).
For sound over HDMI pick the HDMI card from `aplay -l` and add `--audio-device=alsa/...` to `profiles.hdmi.mpv`.
**(unverified on a Pi 1B.)** The Pi 1B drives one output at a time: HDMI when it is plugged in at boot, otherwise composite.

## 7. First test by hand

With the goggles connected and the USB keyboard plugged into the Pi, log in on the **Pi's own screen** (that is the goggles) and run:

```bash
cd /opt/spelunkr-tv
python3 tv.py --profile pi
```

You should see static with `CHECKING SCHEDULE...`, then `CONNECTING TO ARCHIVE.ORG...`, then the show.
Keys: `up`/`down` change channel, digits then `Enter` jump to a channel number, `g` opens the guide, `i` shows the info bar, `q` quits.

Things to check and note: does the picture fill the screen, are the overlays readable and inside the edges (they keep a ~7 % margin),
does it stutter, does it have sound.

**Tuning screens:** press `d` for the diagnostics box (decoder, dropped frames, cache, CPU temperature and power warnings),
`?` for the key list and status, `t` for a test card (handy for centring the picture and checking colours).
A power warning ("UNDERVOLTAGE") means the power supply or cable is too weak; fix that before judging stutter.

**Sound:** the default output may be HDMI. List the cards and pick the 3.5 mm jack:

```bash
aplay -l                       # look for "Headphones" or "bcm2835"
```

then add `"--audio-device=alsa/plughw:CARD=Headphones,DEV=0"` (adjust to what `aplay -l` shows) to `profiles.pi.mpv`.
You can also run `sudo raspi-config` → System Options → Audio.

If it stutters, lighten it up in `config.json` (top level): `"bumps": false`, `"noise": "none"`.

## 8. Start on boot

```bash
sudo systemctl enable --now spelunkr-tv
```

Useful commands:

```bash
sudo systemctl status spelunkr-tv       # is it running?
journalctl -u spelunkr-tv -f            # live log
sudo systemctl restart spelunkr-tv      # after editing config.json
sudo systemctl disable --now spelunkr-tv
```

The service runs on the console (`tty1`), so a USB keyboard plugged into the Pi controls it. It restarts itself if it crashes.

### TV remote over HDMI-CEC (optional, HDMI only)

If the Pi is on HDMI (profile `hdmi`), the TV's own remote can control it:

```bash
sudo apt install cec-utils              # pi/setup.sh already installs it
echo scan | cec-client -s -d 1          # should list your TV; if not, switch CEC on in the TV's settings (Anynet+, Bravia Sync, SimpLink ...)
```

Then set `"cec": {"enabled": true}` in `/opt/spelunkr-tv/config.json` (or add `--cec` to `ExecStart`) and restart the service.
Keys: channel +/- and the d-pad change channel, 0-9 then OK jump to a number, EPG / Guide opens the guide, Info shows the bar, Exit closes the guide
(full table in the README). It also powers the TV on and selects the Pi's input at start-up. **(unverified on a real TV.)**
If a key does nothing, run `cec-client -d 16`, press it, and send me the `key pressed` line. CEC does not exist on composite.

### Web remote (optional)

To control it from your phone, set `"web": {"enabled": true, "token": "pick-a-secret", ...}` in `/opt/spelunkr-tv/config.json`
(or add `--web --web-token pick-a-secret` to `ExecStart` in the service file), restart the service, and open
`http://spelunkr.local:8080/?token=pick-a-secret` on the phone. The same address serves a small JSON API for home automation;
see the README. Keep it on your home network.

### Updating later

From the Mac, repeat the `rsync` of step 3, then on the Pi:

```bash
cd ~/spelunkr-tv && sudo sh pi/setup.sh && sudo systemctl restart spelunkr-tv
```

Note: `setup.sh` overwrites `/opt/spelunkr-tv/config.json`. Keep your Pi flags in `~/spelunkr-tv/config.json` too, or back it up first.

## Troubleshooting

| Problem | Try |
|---|---|
| No picture at all | HDMI unplugged at boot? Check the `config.txt` lines (step 6). Check the cable goes into the yellow jack. |
| Black and white or rolling picture | PAL not applied: `sdtv_mode=2`, or `video=Composite-1:720x576@50ie`. |
| Picture cut off or shifted | `disable_overscan=1`. The goggles may need their own picture settings. |
| `Failed to find a usable DRM primary node` / `/dev/dri` is empty | The KMS display driver is not active, so mpv has nothing to draw on. Check `sudo vclog -m \| grep dterror`: "needs at least 512MB" means the board has too little memory (see "Which Pi"). Otherwise use `dtoverlay=vc4-kms-v3d,composite` in `config.txt` (step 6), reboot, and run `sh pi/probe.sh` again. |
| `mpv` exits straight away | `journalctl -u spelunkr-tv -n 50`. Usually a wrong `--vo` or `--hwdec`: re-run `pi/probe.sh`. |
| Stuttering video | Hardware decode not active (step 5). Set `"bumps": false` and `"noise": "none"`. Lower the cache: `--demuxer-max-bytes=8MiB`. |
| Starts slowly (10+ seconds) | Normal on a cold archive.org file. The screen shows what it is waiting for. A wired network helps. |
| Wrong show, or out of sync with the website | `timedatectl`: the clock must be synchronised (step 2). The client also corrects a clock that is more than 2 s off using the server's time. |
| "PLEASE STAND BY" | The network or archive.org is down or slow. It retries by itself (2, 5, 10, then every 20 seconds). |
| No sound | Step 7, sound. |
| Nothing happens when pressing keys | The keyboard must be plugged into the Pi, and the service runs on `tty1`. Over SSH, run `python3 tv.py --profile pi` by hand instead. |

## Please report back

So I can fix what is wrong in the Pi profile and these instructions, send me:

1. The output of `sh pi/probe.sh`.
2. Which boot-config variant worked (legacy or KMS) and your image version (`cat /etc/os-release`).
3. How it plays: dropped frames, sound, overlay readability, how long a channel takes to start.
