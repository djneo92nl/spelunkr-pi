# spelunkr.tv on a Raspberry Pi (and your Mac)

A small client for [spelunkr.tv](https://spelunkr.tv): 24/7 synchronized "cable TV" channels made from Internet Archive videos.
It works out what is on right now (same maths as the website, see [SCHEDULE.md](SCHEDULE.md)), plays it with `mpv`,
and draws a cable-box info bar, station-break slides and ad breaks, all as text overlays. Personal, non-commercial use.
It caches data and streams from archive.org like the site does; it does not download or redistribute anything.

## Run on the Mac

```bash
brew install mpv
python3 tv.py                  # starts on channel 13 (config.json: start_channel)
python3 tv.py --channel 203    # dial number or id (ray-gun)
python3 tv.py --list
```

Keys (terminal, or inside mpv's window):

| key | does |
|---|---|
| `up` / `down`, `+` / `-` | channel up / down (also the scroll wheel, and the Mac's next / previous media keys) |
| digits, then `Enter` | jump to a dial number (`2`, `0`, `3`, `Enter` for 203) |
| `l` | last channel |
| `i` | info bar; press again while it is showing for the show's details |
| a click in the window | info bar (a click while something is open closes it) |
| `g` | TV guide |
| `n` | what is on now (all channels, one line each) |
| `s` | settings menu |
| `z` | sleep timer: off, 15, 30, 60, 90, 120 minutes |
| `f` (or double-click) | fullscreen |
| `m` (or the play/pause media key), `[`, `]` | mute, volume down, volume up |
| `c` | captions on / off |
| `d` | diagnostics box on / off |
| `?` or `h` | help and status screen |
| `t` | test card |
| `q` | quit (closes the guide first, if it is open) |

On Linux (the Pi) a USB keyboard, remote or IR receiver plugged into the machine is read directly from `/dev/input`, so the keys work however the
client was started (SSH, systemd, the desktop's terminal). The keyboard is grabbed while the client runs, so keys do not also type into the console.
The user needs to be in the `input` group (the default `pi` user is); `"evdev": false` turns this off. Media keys (volume, mute, channel +/-, menu, info) work too.

Start fullscreen with `python3 tv.py --fs`. Extra mpv options: `--mpv-arg=--geometry=1280x720` (repeatable).
It remembers the last channel, volume and mute, and on the Mac the window size and fullscreen, between runs
(`~/.cache/spelunkr/state.json`; set `"remember": false` in `config.json` to turn that off). The window title shows the channel and show.

Python 3.9+, standard library only.

### A proper Mac app

```bash
sh mac/make_app.sh            # builds dist/Spelunkr TV.app: double-click launcher with an icon
open "dist/Spelunkr TV.app"   # log: ~/Library/Logs/spelunkr-tv.log
```

It contains a copy of the client (run the script again after changing code) and finds Homebrew's `mpv` even though apps start with a
small PATH. With `pip3 install rumps` it also shows a **menu bar item** (what is on, next show, all channels with a tick on the
current one, guide, fullscreen, volume, mute, quit). Without `rumps` it simply runs `tv.py`. The menu bar also works from the
terminal: `python3 mac/menubar.py`. **The menu bar item and its launcher are not tested** (installing `rumps` was not possible
while writing them). The app launch itself without `rumps` is tested.

### Check it against the site

```bash
python3 schedule.py computer-chronicles          # what is on now, offset, seek position, URL
python3 schedule.py ray-gun --at 2026-10-31T14:00:00Z
```

Open `https://spelunkr.tv/<channel-id>` next to it and compare. Expect about ±1 s. `tools/verify_guide.py` compares a whole
12-hour guide at once.

## What you see

* **Tuning**: static with the channel number and name, plus a live status line so you can see what it is waiting for:
  `CHECKING SCHEDULE...` (static), then `CONNECTING TO ARCHIVE.ORG...` and `BUFFERING...` on a dark card, each with a seconds counter
  (and a note if archive.org is slow). The card clears when the first frame arrives. The schedule loads in the background,
  so you can keep pressing up/down while a channel is still loading.
* **Info bar** (on tune and on `i`, 6 s): channel, show, year, clock, progress and the next show. In the channel's colour.
* **Station breaks**: the channel's and spelunkr's text slides on black, with the same timing as the site.
* **Ad breaks**: opening slides, then a real period commercial from archive.org at the right offset, then the closing slides.
* **TV guide** (`g`): a Prevue-style grid, 8 channels by 2 hours, drawn over the video (sound keeps playing).
  `up`/`down` pick a channel, `left`/`right` move the window by 30 min (up to +10 h), `Enter` tunes to the selected channel,
  `g` or `q` closes it. Rows load one by one in the background (a channel's manifest is fetched the first time it is shown),
  and the other channels' schedules are freed again when you close it.
* **Boot splash**: the pixel TV and "TUNING IN..." for a couple of seconds at start (`"splash_seconds": 0` skips it).
* **On now** (`n`): every channel with what it is showing, when that ends and what is next; `up`/`down` pick, `Enter` tunes,
  `g` switches to the time grid, `n` closes. Rows load in the background like the guide.
* **Show details** (`i` twice, or `e`): title, year, runtime, season/episode, the site's own description, the IMDb id and a MATURE flag for the show on air.
  In the guide, `e` opens the details of the show selected there.
* **Teletext** (`x`): a Ceefax-style service over the picture: 40x25 characters, blocky mono font, coloured headline bands and the
  red / green / yellow / cyan links along the bottom. Type a three-digit page number, `up`/`down` step through pages, `left`/`right`
  turn sub-pages (they also turn by themselves, like the real thing, until you press one), `Enter` tunes to the channel a page is about,
  `r` `g` `y` `b` (or the coloured keys on a TV remote) follow the links, `x` or Esc leaves. Pages: **100** index, **101** now and next,
  **110** on now (every channel), **200** channels (**2xx** one page each: logo, description, number of programmes, hours of video),
  **300** listings (**3xx** the next 12 hours of one channel, with the commercial breaks), **400-409** this programme and the next ones
  (description, season, IMDb id, mature flag), **500** search and reminders, **600** trivia, music credits and station-break lines,
  **700** news, **800** status, **801** this device, **900** keys. Pages load in the background (the header shows the number while it
  waits); the news and search pages need the network, the rest work from the cache. It needs a monospace font: Menlo on the Mac,
  DejaVu Sans Mono elsewhere (`sudo apt install fonts-dejavu-core`, or set `"teletext_font"`).
* **News** (teletext 700): RSS and Atom feeds from `config.json`: `"feeds": [{"name": "BBC News", "url": "https://feeds.bbci.co.uk/news/rss.xml"}]`
  (up to nine). Page 70n lists the headlines of feed n, 7n0-7n9 are the stories. Cached for 15 minutes (`feed_refresh_s`), and the last copy
  is shown if the network is down.
* **Search** (teletext 501, or the search box on the phone remote): looks through the titles, series names and descriptions of every channel
  and says which ones are on now or in the next 24 hours. Type on a keyboard, or pick letters with the arrow keys and `Enter` (red deletes,
  green searches, yellow clears). Results are pages 510-529; `Enter` on one tunes to its channel. Descriptions need the channels' details files
  (fetched on the first search); without the network, titles still match.
* **Reminders**: on a programme page (4xx) or a search result press the yellow key. A banner appears two minutes before the show, and again when
  it starts; `"reminder_autotune": true` switches the TV to the channel at that moment. Reminders live in teletext page 503 and survive restarts.
* **Favourites and hidden channels**: `a` marks the current channel (or the one selected in the guide) as a favourite, `o` hides it; a
  second press undoes it. Hidden channels are skipped by channel up/down but still tune by number. Settings has "Only favourite channels"
  (channel up/down then visit only favourites) and a "This channel" row. Favourites show a star in the guide, the on-now list and the info bar.
* **Guide extras**: commercials and station breaks are drawn as darker cells between the shows (`ADS` / `BREAK` where there is room),
  and the footer carries the selected show's description once it has loaded.
* **Settings** (`s`): volume, captions, how long the info bar stays, static between channels, station-break slides, black and white,
  sleep timer, only favourite channels, this channel (normal / favourite / hidden) and the audio output (every device mpv can see; the choice is
  remembered and overrides the profile's `--audio-device`). `up`/`down` pick, `left`/`right` change. Choices are remembered between runs.
* **Sleep timer** (`z`, or in the settings menu, or `POST /api/sleep/45`): a minute before it ends a notice says so, and pressing any key
  cancels it ("still watching"). At zero the picture goes to a "goodnight" screen and archive.org streaming stops; any key wakes it back
  to the channel it was on. With CEC it also puts the TV into standby. `"sleep_action": "quit"` exits the program instead.
* **Captions** (`c`): the site's own subtitle files (about 170 tapes: mostly machine-translated anime and foreign commercials; the box says
  `SUBTITLES BY MACHINE` where that is the case). Shows without captions say so. Your choice is remembered.
* **Diagnostics** (`d`): a box in the corner with the playback position against the schedule (drift), decoder in use and whether it is
  hardware, dropped frames, cache, bitrate, how long the last start took, and on a Pi the temperature, load, free memory and
  **undervoltage / throttling warnings**. Stays on while you change channel. This is the screen to look at when tuning the Pi.
* **Help and status** (`?`): the keys, plus the remote's address, this device's IP, clock sync and lineup version.
* **Test card** (`t`): PAL-style colour bars. It is also the background of "PLEASE STAND BY" (`"test_card": false` for plain black).
* **Faster starts**: about a minute before a show ends, the next item's archive.org redirect is resolved and the file warmed with a tiny
  request, so mpv connects straight to the item server (saves about 2 s, more on a cold file). If that shortcut ever fails it falls back
  automatically. **Ad loudness**: ads that carry a `gainDb` are turned down by that amount, as the site does.
* **Trouble**: "PLEASE STAND BY" on the test card, with growing back-off (2, 5, 10, 20 s) while archive.org is slow or down.
  Schedule data keeps working from the disk cache.

## Web remote and JSON API

Off by default. Start it with `python3 tv.py --web` (port 8080; `--web-port`, `--web-token`), or set `web.enabled` in `config.json`.
It prints the address, for example `http://192.168.1.20:8080/`; open that on your phone for a remote with channel buttons,
volume, guide and a live "now playing" card.

```
GET  /api/status             what is on now
GET  /api/channels           channel list
POST /api/channel/<dial|id>  e.g. /api/channel/203 or /api/channel/ray-gun
POST /api/key/<name>         up down last info guide fullscreen mute volup voldown left right enter
POST /api/volume/<up|down|mute>
POST /api/sleep/<minutes>    sleep timer, 0 = off
POST /api/page/<100-999>     open that teletext page
POST /api/search/<words>     search every channel; the results open in teletext
```

Keys also include `captions diag help testcard now details menu sleep bar`, `text red green yellow blue` (teletext and its colour keys)
and `favourite hide favs_only`. The phone page has buttons for these and a search box. `/api/channels` says which channels are favourites or hidden.

```bash
curl http://tv.local:8080/api/status
curl -X POST -H "X-Remote: 1" http://tv.local:8080/api/channel/203
```

`/api/status` returns, for example: `{"channel": {"dial": 13, "id": "computer-chronicles", "name": "COMPUTER CHRONICLES"},
"state": "playing", "title": "Computer Games", "year": "1984", "offset_s": 1610.7, "remaining_s": 158.3,
"next": {"title": "IBM Clones", "start_ms": 1793473423750}, "volume": 110, "muted": false, ...}`. `state` is one of
`tuning`, `loading`, `playing`, `commercial`, `break`, `error`.

**Safety.** POSTs must carry a custom header (`X-Remote: 1`, or `X-Token`), which browsers will not send from another website,
so a web page elsewhere cannot flip your channels. Set `web.token` to also require a secret for everything (`?token=...` on the page URL,
or an `X-Token` header). There is no encryption; keep it on your home network and do not forward the port. For Home Assistant, use
`rest_command` / `rest` sensors against these endpoints.

## TV remote over HDMI-CEC (Pi on HDMI)

With the Pi on HDMI, the TV's own remote can control the client, so no extra hardware is needed. Install `cec-utils` on the Pi
(`sudo apt install cec-utils`; `pi/setup.sh` does it), make sure CEC is switched on in the TV's settings (it is called Anynet+, Bravia Sync,
SimpLink, EasyLink, ... depending on the brand), then start with `python3 tv.py --cec` or set `"cec": {"enabled": true}` in `config.json`.

| TV remote | does |
|---|---|
| channel +/-, d-pad up/down, page up/down | channel up / down |
| 0-9, then OK | jump to a dial number |
| OK / Select | info bar |
| Exit | close the guide |
| EPG, Guide, Menu, green | TV guide (left/right then move the time window) |
| Info, yellow | info bar (again: show details) |
| Contents menu | what is on now |
| Setup menu | settings menu |
| Subtitle | captions on / off |
| Previous channel, red | last channel |
| blue | mute |
| volume +/-, mute | volume / mute (normally the TV handles these itself) |

At start-up it asks the TV to power on and switch to the Pi's HDMI input (`"wake_tv": false` turns that off).
CEC does not exist on composite, so this is for the HDMI setup only. **Untested on real hardware**: the key parser and the process handling are
tested against a fake `cec-client` (a held key counts as one press, digits, OK, Exit and the colour keys map as above), but not with a
real TV. If a key does nothing, run `cec-client -d 16` on the Pi, press it, and send me the line that says `key pressed`.

## Configuration

`config.json`. Important keys: `start_channel`, `bar_seconds`, `bumps` (set false to skip slides),
`noise` (`"lavfi"` or `"none"`), `drift_s` (re-seek if playback is more than this many seconds off the schedule),
`remember`, `web` and `cec` (see above), `splash_seconds`, `sleep_action` (`blank` or `quit`), `favourites_only`, `reminder_autotune`,
`feeds` (teletext news), `teletext_font`, and `profiles.<name>.mpv` (the exact mpv command line; `{sock}` and `{inputconf}` are filled in).
Profiles: `mac`, `pi` (composite), `hdmi` (Pi on HDMI/TV), `desktop` / `desktop-lite` (Linux laptops and PCs), and black-and-white versions `mac-bw`, `pi-bw`, `hdmi-bw`, `desktop-bw`. The overlays follow the window or display shape (4:3 or 16:9), also when you resize.
Choose a profile with `--profile pi` or the `profile` key. Cache: `~/.cache/spelunkr` (lineup refreshed every 10 min,
manifests kept per version).

## Checking a machine: `--doctor`

`python3 tv.py --doctor` checks that mpv is new enough, the cache folder is writable, data.spelunkr.tv and archive.org answer (and HTTPS certificates
verify, and the clock is right), the audio device the profile asks for exists, a USB keyboard is readable (the `input` group), the display device is
usable for DRM profiles, CEC is installed if enabled, the Pi is not under-voltage or hot, the teletext font is installed and the news feeds parse.
Each problem comes with the command that fixes it, and the exit code is 1 if anything failed. It needs no display, so run it over SSH on a fresh Pi.

## Laptops and other Linux machines

Any Linux machine with a desktop (X11 or Wayland) can be a TV: an old netbook, a Pinebook, a spare PC. **Untested on real Linux hardware so far** (the logic is tested on the Mac).

```bash
sudo apt install mpv python3        # Debian / Ubuntu names
python3 tv.py --profile desktop     # starts fullscreen the first time; f toggles; the window size is remembered
```

* `desktop`: software decoding, plus hardware decoding where mpv finds a safe one (VA-API). Right for anything with a few reasonable cores (e.g. a Pinebook's quad A53).
* `desktop-lite`: for weak machines (an Atom N270 netbook, an 800 MHz CPU): cheaper decoding settings and the Xv video output instead of OpenGL, since old Intel graphics cannot run mpv's `gpu` output well. Needs X11; without X use `--vo=drm` instead of `--vo=xv`. Check dropped frames with `d`.
* `desktop-bw`: the black-and-white version.
* A 32-bit CPU (Atom N270, Pentium M) needs a 32-bit Linux. Debian 12 (bookworm) still ships i386. Newer Debian dropped it.
* To start with the desktop, add a file `~/.config/autostart/spelunkr.desktop` containing `[Desktop Entry]`, `Type=Application`, `Name=Spelunkr TV`
  and `Exec=python3 /path/to/tv.py --profile desktop`. The keyboard, remote and web remote work as everywhere else.
* The Windows and Mac-specific parts (the `.app`, the menu bar) do not apply. Windows is not supported.

## Black-and-white screens

For a monochrome display use `--profile pi-bw` (or `hdmi-bw` / `mac-bw`), or add `--mono` to any profile. The picture is rendered in
grayscale (`--saturation=-100`) and **every overlay colour** (info bar, guide, slides, test card) is converted to a matching gray, so
nothing disappears when two colours would look alike. On the Pi's composite output also add `sdtv_disable_colourburst=1` to `config.txt`
for a sharper monochrome picture. If the picture is still coloured on your decoder, add `--vf=lavfi=[hue=s=0]` to the profile's `mpv` list
(uses the CPU).

## Raspberry Pi 1B

**Status: the Pi profile is an untested starting point.** Everything above was tested on a Mac. Nothing here has run on a Pi yet.
Known unknowns: whether `mpv` on a Pi 1B (ARMv6, 512 MB) decodes these streams without dropping frames, which
hwdec/vo combination works, and whether the overlays are too heavy there. `pi/probe.sh` answers the first two.

**Step-by-step instructions, from a blank SD card: [INSTALL-PI.md](INSTALL-PI.md).** The summary below is the short version.

### 1. Boot config for PAL composite

Legacy firmware video path (`/boot/firmware/config.txt`, or `/boot/config.txt` on older images):

```
sdtv_mode=2          # PAL
sdtv_aspect=1        # 4:3
disable_overscan=1
enable_tvout=1       # only needed on some images; harmless otherwise
```

On newer images using the KMS driver (`vc4-kms-v3d`), composite is instead enabled with `dtoverlay=vc4-kms-v3d,composite`
and `video=Composite-1:720x576@50ie` on the kernel command line (`cmdline.txt`). `--vo=drm` needs KMS. Which of the two applies
depends on the image; check on the board and adjust. `sudo pi/setup.sh --apply-boot-config` appends only the first block (with a backup).

### 2. Install

On the Pi (Raspberry Pi OS Lite, 32-bit), from this folder:

```bash
sudo sh pi/setup.sh          # installs mpv + python3, copies to /opt/spelunkr-tv, installs the systemd unit
sh pi/probe.sh               # tries vo/hwdec combinations for 15 s each and reports dropped frames
```

Put the winning flags into `profiles.pi.mpv` in `/opt/spelunkr-tv/config.json`, then:

```bash
sudo systemctl enable --now spelunkr-tv
journalctl -u spelunkr-tv -f
```

The service runs on `tty1`, so a USB keyboard plugged into the Pi controls it. GPIO buttons or a USB remote can be added by writing
another input class (see the top of `inputs.py`).

### If the overlays are too heavy on the Pi 1B

Set `"bumps": false` (no slide screens) and `"noise": "none"`; the info bar is the cheapest overlay. If even that drops frames,
the Pi 3 fallback will not need any change.

## Logs and crash recovery

`~/.cache/spelunkr/tv.log` records start-ups, errors and any thread that dies. mpv's own log and stderr are kept next to it (`mpv.log`, `mpv.err`).
If mpv exits abnormally the client restarts it and tunes back in (up to four times in five minutes), so an unattended box recovers by itself.
An error handling a single key press or a single tick of the main loop is logged and the loop carries on.
The guide keeps the last 8 channels' built schedules in memory (`guide_cache_channels`) so reopening it is quick, and building one channel's
schedule no longer holds up the playback loop.

## Files

| file | what |
|---|---|
| `tv.py` | the client: tuning, scheduling loop, retries |
| `schedule.py` | the website's schedule algorithm in Python (also a CLI) |
| `osd.py` | info bar, bump slides, cards (ASS overlays) |
| `mpvctl.py` | mpv JSON-IPC controller |
| `inputs.py` | keyboard / mpv-window input; add GPIO here |
| `webremote.py` | web remote + JSON API |
| `subs.py` | captions: the site's subtitle files as SRT for mpv |
| `prefetch.py` | resolves the next item's archive.org URL ahead of time |
| `teletext.py` | the Ceefax-style renderer: 40x25 grid, colour codes, pages, logo as block graphics |
| `ttpages.py` | what is on each teletext page |
| `feeds.py` | RSS / Atom reader for the news pages |
| `search.py` | search across every channel's titles and descriptions |
| `doctor.py` | the `--doctor` checks |
| `tests/` | `python3 -m unittest discover -s tests` (no network, no mpv needed) |
| `mac/` | `make_app.sh` (builds the .app), `menubar.py` (menu bar item), `make_icon.py` |
| `pi/` | probe script, setup script, systemd unit |
| `tools/verify_guide.py` | compare against the site's guide |

## Not done yet

* Audio for `vacation-slides` (audio overlay channel) and the bump music; video and slides only.
* Prefetch: measured to help (a start took 4 s through the shortcut against 37 s cold), but each file is different, so treat the numbers as indicative.
* Everything on the Pi hardware, including the teletext font and layout on a composite display (the overlays are checked on a Mac only).
* Teletext: pages are rendered with a stretched monospace font, so columns can drift by a pixel or two with other fonts; no mosaic
  graphics beyond the channel logos; search has no punctuation keys.
* The menu bar item (needs `rumps`; written but never run).
* Window position is not remembered on the Mac (mpv does not expose it); size and fullscreen are.
