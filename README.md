# AstroDashboard

Library, diagnostics, live monitoring and planning for the observatory. Design and roadmap: [PLAN.md](PLAN.md).

**Status:** Phases 0–3 are done: scaffold, indexer + Library, Diagnostics (astro-eval measurements plus the dashboard's
own detectors and learned horizon). Planning, Live and the organizer are placeholders.

Diagnostics use the sibling checkout `D:\Python\AstroSubQualityEvaluation` (set `[diagnostics] astro_eval_path`
in `astrodashboard.toml` to move it). It is loaded by path, not installed, and only its measuring/scoring code
is used — its report writers are never called, so nothing is written next to your frames.

## Run

Two services (registered in `project-dashboard.json`):

```powershell
# Engine — indexes G:\Astro (read-only) and serves the API on :3007
cd D:\Python\AstroDashboard\engine
uv run astrodash-engine

# Web UI on :3006
cd D:\Python\AstroDashboard\web
npm install   # once
npm run dev -- --port 3006
```

Open http://localhost:3006. The engine scans on startup (about 8 s cold, under 1 s after that thanks to the
header cache in `var/astrodash.db`); **Rescan** on the Library page picks up new nights.

## Data safety

The engine never writes to the data root. It only lists directories and reads FITS/XISF *headers*. Its own files
(header cache, thumbnails) live in `var/`. `engine/tests/test_safety.py` fails if any module gains a
file-mutating call outside the allow-list.

## Tests

```powershell
cd engine; uv run pytest      # synthetic data root, never touches G:\
cd web; npm run typecheck
```
