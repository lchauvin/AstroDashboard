# AstroDashboard

Library, diagnostics, live monitoring and planning for the observatory. Design and roadmap: [PLAN.md](PLAN.md).

**Status:** Phases 0–4 are done: scaffold, indexer + Library, Diagnostics (astro-eval measurements plus the dashboard's
own detectors and learned horizon), and Planning (sites with a map, sky calibrated from your own subs, tonight's
ranking with a moon-aware filter suggestion, a 30-night calendar, new target ideas, SNR curves and goals per filter).
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
and set their addresses in Settings → Live · Ingest. To try Live in daylight, start the engine with
`uv run astrodash-engine --fake-observatory` (simulated NINA and PHD2; the guiding reacts to its parameters).
The Guiding page's AI advisor uses the model set in the same Settings tab; API keys go in `.env` (see `.env.example`).
To have NINA react to clouds, connect its safety monitor (Equipment › Safety Monitor) to "AstroDashboard sky monitor",
an ASCOM Alpaca device the engine serves on port 3008 (found by Alpaca discovery; allow it through this PC's firewall),
and use Loop While Safe / Loop While Unsafe in the sequence. The steps are in Settings → Live · Ingest.
For the weather forecast on the Planning page, set `ASTROSPHERIC_API_KEY` (an Astrospheric Professional key, from
My Profile) in `.env` at the project root (copy `.env.example`; git-ignored) or in the engine's environment; forecasts are cached per site for an hour and only cover the RDPS domain
(North America, Greenland, Iceland, Ireland, UK). Optionally set `TELESCOPIUS_API_KEY` (Telescopius → account settings →
API Keys; patrons and sponsors) the same way: target pages then show the object's reference image, catalogue IDs, size and
surface brightness, Planning lists your Telescopius target lists and the month's sky events. Everything is cached in the engine. The engine scans on startup (about 8 s cold, under 1 s after that thanks to the
header cache in `var/astrodash.db`); **Rescan** on the Library page picks up new nights.

To get tonight's Planning verdict on your phone, set a topic in Settings → Notifications and subscribe to it in the free
**ntfy** app, then run `scripts\install-notify-task.ps1` once: a scheduled task checks at 15:00 and 19:30 (the dashboard has
to be running) and notifies only for good nights, with an update if the verdict changes. Runs are logged in `var\notify.log`.

## Data safety

The engine never writes to the data root, with one bounded exception: auto-ingest in **Auto** mode moves finished
frames from `.Download` into targets' `.SessionData\NIGHT_<date>` folders (and copies shared flats). That code is
confined to `engine/astrodash/ingest/fileops.py`, never overwrites or deletes, and is journaled and undoable per night.
Organizer plans you approve are applied by `engine/astrodash/organizer/apply.py` under the same rules.
Otherwise the engine only lists directories and reads FITS/XISF *headers* (plus the pixels of a
few raw biases for the sky pedestal, of frames you preview, and of the dark sets you add to a light-leak test, which
may sit outside the data root). Its own files (header cache, thumbnails, sites, goals, settings, light-leak arrays in
`var/leaks`) live in `var/`. `engine/tests/test_safety.py` fails if any module gains a
file-mutating call outside the allow-list.

## Tests

```powershell
cd engine; uv run pytest      # synthetic data root, never touches G:\
cd web; npm run verify        # typecheck + vendored SNR/ephemeris checks + planner checks
node --conditions react-server --import tsx scripts/smoke-plan.ts [YYYY-MM-DD]   # tonight's plan against the running engine
```
