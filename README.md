# AstroDashboard

Library, diagnostics, live monitoring and planning for the observatory. Design and roadmap: [PLAN.md](PLAN.md).

**Status:** Phases 0–4 are done: scaffold, indexer + Library, Diagnostics (astro-eval measurements plus the dashboard's
own detectors and learned horizon), and Planning (sites with a map, sky calibrated from your own subs, tonight's
ranking with a moon-aware filter suggestion, a 30-night calendar, new target ideas and goals per filter).
Phase 5 adds Live (its own connection to NINA and PHD2: sequence, last image, equipment, guiding with an optional AI advisor) and auto-ingest from `G:\Astro\.Download`,
which runs in **Dry-run** until you switch it to Auto on the Live page (PLAN.md §5.2). Phase 6 adds the Organizer (Library → Organizer): a lint of every target against the canonical layout, and
migration plans you read operation by operation and approve by typing the plan id. Moves only (legacy sessions go to
`.Trash\<plan>`), journaled in `G:\Astro\.Dashboard\journal.jsonl`, and undoable. Nothing has been applied to
`G:\Astro` yet; that is your call, target by target (PLAN.md §3.3).

The **Light-leak finder** (Library → Calibration library → Light-leak finder) works in two phases. **Phase 1, spot
checks:** single frames (scope capped, e.g. 60 s Luminance), each with a flashlight sweeping over one spot of the rig,
plus a few with the flashlight off. Each frame is judged on its own against those: light raises the level *and* the
pixel noise (its shot noise), while this camera's bias steps raise the level only, so the noise tells a leak from a
step even when the moving flashlight leaves no pattern. **Phase 2, dark sets:** the rig as it is, then a set after
each fix, each with a comment saying what changed and compared with the set before it: frame levels and noise, plus
the structure of the difference of the two masters (gradient, edges beyond the gradient, 3×3 zones, patchiness), each
against a noise floor from the sets' even/odd half masters, shown stretched hard with profiles and per-frame charts.

**Framing** (Library → target → Sessions, each night's Diagnostics, Live): every night is checked against the previous
night of the same target (and mosaic panel) by matching stars. ✓ when the shift and rotation, measured at the frame
corners, stay within the nights' spread (dither and drift); ⚠ up to twice it; ✗ beyond. Live checks the newest light the
same way, so a mis-centred or turned framing shows on the first frame of the night. A night's Diagnostics plots each light's
framing through the night against its dither (shifts only), so a drift such as field rotation stands out (PLAN.md §4.4).

**Goals** (Library or Planning → target → Goals): per filter, **Hours**, **Match** or **None**. Match makes another Library
target the goal in that filter ("as good as my Heart Nebula in Hα"). Both targets are measured from their kept subs: each
light binned, flat-fielded with its night's flat, the light-pollution gradient removed, then the nebula's contrast over the
noise, squared so it adds up over subs. The hours needed are the reference's quality over this target's quality per hour, and
they appear once this target has an analysed night in that filter (PLAN.md §6.7). No SNR is estimated anywhere any more; the
moon-aware filter choice comes from how much the moon brightens each filter's sky.

**Center on it** (Library → target → Sessions, under each night): one button per night (per mosaic panel) opens a menu with two
actions, both on that night's target coordinates, the ones its sequence was told to point at before any dither (the lights' `OBJCTRA`/`OBJCTDEC`/`OBJCTROT`).
**Center now** slews and centres with NINA's own Center (its plate-solve pointing tolerance applies). If the sequence is running,
a warning asks first; on Continue the dashboard waits for the exposure in progress to be saved, stops the sequence (the Advanced
API has no pause), updates the target container's coordinates, slews and centres, waits until PHD2 has guided without settling
for 15 s (asking NINA to start guiding if it did not restart by itself), then starts the sequence again with `skipValidation`.
If anything fails after the stop, the sequence stays stopped, Live's Sequence card says why, and an ntfy notification goes out
when notifications are on. With no sequence running it only slews and centres; the sequence is not touched. **Update sequence**
only writes the coordinates into the sequence's target container, running or not, so its own Slew and center goes there. The
change lives in NINA's memory: save the sequence in NINA to keep it. The target's name and rotation are kept, so `OBJECT` and
auto-ingest routing don't change. Any name the target had on the revision's nights matches (e.g. "Flaming Star Nebula" for a
sequence now called "IC 405"). How close nights land is still limited by NINA's pointing tolerance (Options › Plate solving):
1′ is about 25 px at 2.4″/px.

## Run

Two services (registered in `project-dashboard.json`). `.\start.ps1` (or double-click `start.cmd`) starts both,
opens the browser, and stops the engine when you Ctrl+C the web UI (`-NoBrowser`, `-EnginePort`, `-WebPort` to
override). By hand:

```powershell
# Engine — indexes G:\Astro (read-only) and serves the API on :3007
cd D:\Python\AstroDashboard\engine
uv run astrodash-engine

# Web UI on :3006
cd D:\Python\AstroDashboard\web
npm install   # once
npm run dev -- --port 3006
```

Open http://localhost:3006. For Live, enable NINA's Advanced API plugin and PHD2's server (Tools → Enable Server)
and set their addresses in Settings → Live · Ingest, along with the folder NINA saves its frames into (the SMB share's
local path on this PC; `G:\Astro\.Download` by default, and it must be on the data root's drive). To try Live in daylight, start the engine with
`uv run astrodash-engine --fake-observatory` (simulated NINA and PHD2; the guiding reacts to its parameters).
The Guiding page's AI advisor uses the model set in the same Settings tab; API keys go in `.env` (see `.env.example`).
To have NINA react to clouds, connect its safety monitor (Equipment › Safety Monitor) to "AstroDashboard sky monitor",
an ASCOM Alpaca device on port 3008 (found by Alpaca discovery; allow it through this PC's firewall),
and use Loop While Safe / Loop While Unsafe in the sequence. The steps are in Settings → Live · Ingest.
NINA cannot image without it, so it runs apart from the engine (`uv run astrodash-skymonitor`, a supervisor that restarts
it) and keeps answering NINA when the engine is stopped or has crashed (it then says clear). The engine starts it when
nothing answers on the port; run `scripts\install-skymonitor-task.ps1` once so a scheduled task also starts it at logon
and every minute. If NINA drops it during a sequence anyway, the engine reconnects it and notifies your phone (ntfy).
For the weather forecast on the Planning page, set `ASTROSPHERIC_API_KEY` (an Astrospheric Professional key, from
My Profile) in `.env` at the project root (copy `.env.example`; git-ignored) or in the engine's environment; forecasts are cached per site for an hour and only cover the RDPS domain
(North America, Greenland, Iceland, Ireland, UK). Optionally set `TELESCOPIUS_API_KEY` (Telescopius → account settings →
API Keys; patrons and sponsors) the same way: target pages then show the object's reference image, catalogue IDs, size and
surface brightness, Planning lists your Telescopius target lists and the month's sky events. Everything is cached in the engine. The engine scans on startup (about 8 s cold, under 1 s after that thanks to the
header cache in `var/astrodash.db`); **Rescan** on the Library page picks up new nights.

Finished targets can move to another disk and stay in the Library: add that disk's folder in Settings → Library.
Move each target folder whole and keep its name (diagnostics and goals follow the name). Archive folders are read only:
the organizer, quarantine and auto-ingest only work under the data root.

To get tonight's Planning verdict on your phone, set a topic in Settings → Notifications and subscribe to it in the free
**ntfy** app, then run `scripts\install-notify-task.ps1` once: a scheduled task checks at 15:00 and 19:30 (the dashboard has
to be running) and notifies only for good nights, with an update if the verdict changes. Runs are logged in `var\notify.log`.

## Data safety

The engine never writes to the data root, with one bounded exception: auto-ingest in **Auto** mode moves finished
frames from `.Download` into targets' `.SessionData\NIGHT_<date>` folders (and copies shared flats). That code is
confined to `engine/astrodash/ingest/fileops.py`, never overwrites or deletes, and is journaled and undoable per night.
Organizer plans you approve are applied by `engine/astrodash/organizer/apply.py` under the same rules.
Otherwise the engine only lists directories and reads FITS/XISF *headers* (plus the pixels of a
few raw biases for the sky pedestal, of frames you preview, of lights for the framing check, and of the dark sets you
add to a light-leak test, which may sit outside the data root). Its own files (header cache, thumbnails, sites, goals,
settings, star positions, light-leak arrays in `var/leaks`) live in `var/`. `engine/tests/test_safety.py` fails if any module gains a
file-mutating call outside the allow-list.

## Tests

```powershell
cd engine; uv run pytest      # synthetic data root, never touches G:\
cd web; npm run verify        # typecheck + vendored SNR/ephemeris checks + planner checks
node --conditions react-server --import tsx scripts/smoke-plan.ts [YYYY-MM-DD]   # tonight's plan against the running engine
```
