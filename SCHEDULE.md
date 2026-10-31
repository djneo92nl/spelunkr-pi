# How spelunkr.tv decides what is on

Reverse-engineered from the site bundle (`/assets/index-*.js`), ported in `schedule.py`.
Everything is deterministic arithmetic on wall-clock time. There is no server-side "now playing".

## Inputs

| File | Gives |
|---|---|
| `https://data.spelunkr.tv/lineup.json` | channel list, and per channel `versions[]`: `{v, manifest, epochMs, effectiveAtMs, startIndex}` |
| `manifests/<ch>/vNN.json` | `items[]`, channel `bumps[]`, optional `block` / `ads` |
| `platform/<hash>.json` (from manifest `platform`) | platform-wide `bumps[]` (the `spelunkr.tv` slides) |

**Find the current manifest from `lineup.json`, not by guessing `vNN`.** Several versions may be listed;
`lineup.json` is re-generated whenever a version changes, so refresh it every few minutes.

## Algorithm (engine 5)

1. **Pick the version** for time `t` (ms): the highest `v` whose `effectiveAtMs <= t`.
2. **Cycle** = the whole item list played once, each item followed by a *break*.
   Cycle 0 starts at `epochMs` and begins at `items[startIndex]`.
   Cycles 1, 2, ... start at item 0. Cycle `k+1` starts exactly when cycle `k` ends,
   so the schedule repeats forever with no drift.
3. **Item slot** = `item.duration * 1000` ms. `duration` is already the trimmed length
   (`outS - inS`). Play the file from `inS + offset`.
4. **Break after every item, including the last** (no block configured):
   `len = 3500 + S(ch) + 1000 + 1000 + 300 + S(plat) + 1000 + 1000`,
   where `S(bump) = sum(max(2500, 1500 + 55 * len(slide.text)))` (UTF-16 length).
5. **Which bumps** (needs the exact PRNG, see `schedule.py`):
   * hash `Fn(s)`: `h = h*31 + charCode` in int32.
   * PRNG: mulberry32 seeded with the hash.
   * seed string `"{v}.{cycle}"`, `U` = absolute item index.
   * platform bump: `pool[floor(rnd(Fn("spelunkr-break-{U}-{seed}"))() * len)]`.
   * channel bump (engine >= 5): a **deck**. It is a shuffled copy of the pool, drawn in order.
     The deck is reshuffled when empty with seed `Fn("{ch}-deck-{seed}-{rounds}")`.
     Recently played bumps are pushed to the back of the new deck.
     **The deck state carries from one cycle to the next**, so cycles must be built in order from 0.
   * seasonal bumps (`date_range`) swap the pool in or out depending on the date at that moment.
6. **Block channels** (`block` in manifest, e.g. homeroom, fallout-shelter): between items, until
   `block.lengthMs` of programming has accumulated, an "up next" card (`cardMs`, or a seeded sting's
   `lengthMs`) replaces the bump break.
7. **Clock**: the site compares your clock with the HTTP `Date` header and corrects it if they differ by more than 2 s.
   `schedule.py` does the same.

## Playing it

* URL: `https://archive.org/download/<id>/<urlencoded file or mp4>` → 302 to a `*.archive.org` item server (verified).
* `moov` is the byte size of the MP4 header; the site uses it to fetch the header first. `mpv` doesn't need it.
* During a bump break the site shows text slides over audio. A client can show black or noise until the break ends.

## Ad breaks (engine 5, `ads` in the manifest)

An ad break replaces the normal bump break when the show just ended is not `offAir`, the next show is not `offAir`,
and at least `ads.gapMs` (default 20 min) of programming has played since the last break.
It becomes three timeline entries: channel-bump half (`3500 + slides + 2000` ms), the **ad clip**
(`slice.lengthMs`), platform-bump half (`slides + 2000` ms).

The clip is `https://archive.org/download/<slice.tape>/<slice.file>`, played from `slice.inMs/1000 + offset`.
Which slice is chosen is a deterministic "planner" (`AdPlan` in `schedule.py`):

* each slice gets a stable phase 0..1e6 from a hash of its id; slices are ordered by phase;
* slices are filtered by year tolerance around the show's year (3 yrs, 6 yrs, optional decades, then anywhere),
  taking the first tier that has enough slices for the channel's break rate ("healthy");
* the phase of "now" (time modulo a round length, 336 h by default, shifted by `ads.start`) selects a position in that
  ring; the nearest slice not recently used wins. "Recent" is carried from cycle to cycle, like the bump deck.

Each manifest version names **its own platform file**, so platform bumps must be loaded per version.

## Verified

Compared with the live site on 2026-10-31 by reading its "next 12 hours" guide (start time of every show, to the minute)
and checking my computed start times against it:

| channel | features | shows compared | mismatches |
|---|---|---|---|
| computer-chronicles | bump decks, cycle 1 | 7 (+ elapsed/remaining to 1 s) | 0 |
| ray-gun | ads | 23 | 0 |
| meddling-kids | ads + offAir | 28 | 0 |
| rubber-hose | block + ads | 91 | 0 |
| homeroom | block | 52 | 0 |

Start times only line up if every break length (hence bump pick, ad pick and ad clip length) is right. All 35 channels
compute without errors, 60 days ahead as well.

## Not verified

* Cycle rollover with ads/decks carry beyond cycle 1 (the 12 h guide mostly stays inside one cycle). The carry logic follows the code.
* Seasonal `date_range` bumps and a new version going live mid-cycle: ported, not exercised.
* `vacation-slides` (audio overlay) timing is the plain case; its audio is not modelled.
* Times before a version's `epochMs`: the site builds backwards cycles; not ported (not needed for "now").
* Sub-second sync with the site is untested; expect about ±1 s.
* Python's default urllib User-Agent gets a 403 from `data.spelunkr.tv`; a descriptive UA works (used in `schedule.py`).
