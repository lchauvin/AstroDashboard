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

Planning vendors AstroPlanner's SNR and ephemeris modules (`web/src/lib/snr`, `web/src/lib/astro`) and its deep-sky
catalog (`web/data/catalog`, OpenNGC CC-BY-SA-4.0 + VizieR) — copied once with provenance headers, never read from
the sibling at runtime. Hα survey values come from the CDS hips2fits service (public, read-only) and are cached in
`var/`. The sites map uses OpenStreetMap tiles.

Diagnostics use the sibling checkout `D:\Python\AstroSubQualityEvaluation` (set `[diagnostics] astro_eval_path`
in `astrodashboard.toml` to move it). It is loaded by path, not installed, and only its measuring/scoring code
is used — its report writers are never called, so nothing is written next to your frames.

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
few raw biases for the sky pedestal, and of frames you preview). Its own files (header cache, thumbnails, sites, goals,
settings) live in `var/`. `engine/tests/test_safety.py` fails if any module gains a
file-mutating call outside the allow-list.

## Tests

```powershell
cd engine; uv run pytest      # synthetic data root, never touches G:\
cd web; npm run verify        # typecheck + vendored SNR/ephemeris checks + planner checks
node --conditions react-server --import tsx scripts/smoke-plan.ts [YYYY-MM-DD]   # tonight's plan against the running engine
```
