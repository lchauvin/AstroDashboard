# AstroDashboard — Design & Implementation Plan

One place to answer four questions about the hobby:

1. **Library** — what data do I have for each target, and is it complete enough to process?
2. **Diagnostics** — is a given session's data any good (lights and flats)?
3. **Live** — how is tonight's session going?
4. **Planning** — what should I shoot next, with which filter, and for how long?

Status: **Phases 0–6 implemented (2026-10-09)** — scaffold, header indexer, Library (targets, target pages, calibration library), Diagnostics v1 (astro-eval jobs, timeline, frame table, previews) and v2 (findings: clouds, obstruction, twilight, moon, dew, focus drift, tracking, mispointing/rotation, flat↔light check; learned horizon), Planning (sites, sky calibrated from your subs, ranking, moon-aware filter suggestion, calendar, ideas, SNR curves, goals; see §6.5 for what was built and how it differs), Live (direct NINA + PHD2 since 2026-10-14, §5.3) and auto-ingest from `.Download` in Dry-run by default (§5.2), and the Organizer (§3.3): lint, dry-run plans, typed approval, journal, undo, trash. No plan has been applied to `G:\Astro`.

---

## 0. Locked decisions

| Topic | Decision |
|---|---|
| File safety | **Read-only by default.** Any reorganization (move / rename / quarantine) is shown as a dry-run plan and applied only after explicit approval in the UI. **The dashboard never deletes anything** — "remove" always means "move to a quarantine folder". |
| Live | **Self-sufficient** (decided 2026-10-14, §5.3): the engine connects to NINA's Advanced API and PHD2's event server itself. AstroController is no longer needed. |
| Stack | **Next.js 15 (TypeScript) UI + Python FastAPI "engine"** — two local services. |
| Sites | **Home + dark-site trips** → a site selector with per-site sky brightness. |
| Auto-ingest | The engine runs on the machine with `G:\` and auto-moves finished frames from `G:\Astro\.Download\` into the right target's `.SessionData\NIGHT_<date>\` (§5.1). This is the only automatic write, and it's bounded: moves only, never overwrites or deletes, journaled and undoable, with a dry-run mode. |
| Data layout | No year level; a revision is its own target folder (`<Target> R2`); sessions are `NIGHT_<date>`; mosaics use `<ID>_Panel_<n>` (e.g. `M31_Panel_1`) (§3.2). |
| Design | AstroBlog's **"Industry"** design system (tokens ported from `AstroBlog/src/styles/tokens.css`). |

---

## 1. What is in `G:\Astro` today (surveyed 2026-10-08, read-only)

### 1.1 Root

```
G:\Astro
├── .Calibration\<YYYY-MM-DD>\          ← dark/bias LIBRARY, one folder per library build
│     ├── BIAS\<…>\*.fits               (e.g. BIAS\Dark, BIAS\Dark\new, BIAS\0.25s)
│     ├── DARK\<…>\*.fits
│     └── Masters\<temp>C\masterBias_-10.xisf, masterDark_-10_300s.xisf …
├── .Download\  .Photoshop\  .PixInsight\ (shared .pxiproject files)
├── .NINA\Targets\*.json  .NINA\Templates\{LRGB,SHO}.template.json
├── NewProcessing.bat
└── <Catalog ID> (<Common name>)\       ← one folder per TARGET, e.g. "IC 1848 (Soul Nebula)"
```

### 1.2 Per target (dominant layout)

```
<Target>\
├── <YEAR>\                              ← imaging season (2025, 2026)
│   ├── .SessionData\<session>\…         ← RAW lights + flats, one folder per night
│   ├── .Calibration\<filter>\<date>\    ← calibrated flats (*_c.xisf) + MasterFlat_<F>_<YYYYMMDD>.xisf
│   ├── .Finished\                       ← final renders (xisf / tif / png / jpg / psd)
│   ├── .MispointedFrames_Quarantine\    ← (M 31 only) quarantined subs, mirrors .SessionData layout
│   └── 1. Blinked … 7. Integrated       ← PixInsight manual-pipeline stages
├── WBPP\{calibrated,logs,master,registered}   (some targets; also WBPP2, "hjk")
├── FlatWizard\FLAT                      (some targets)
└── *.pxiproject
```

### 1.3 Session folders — three generations coexist

| Generation | Folder name | Inside |
|---|---|---|
| Old | `2025-09-26`, `SESSION_2025-08-08` | `LIGHT\<F>\`, `FLAT\<F>\` |
| Current NINA | `NIGHT_2026-09-18` | `<NINA object name>\LIGHT\<F>\`, `FlatWizard\FLAT\<F>\`, sometimes `Snapshot\` |
| Mosaic | `NIGHT_2026-08-20` | `M 31 Panel 1\LIGHT\<F>\`, `M 31 Panel 2\…`, … |

Special prefixes seen: `[GARBAGE] 2025-12-27`, `.OLDCAMERA-2025-07-26`, `.OLDCAMERA-SESSION_2025-08-01`. Inside filter folders: `_REJECTED\`, `Accepted\`, and astro-eval reports (`astro_eval_report*.csv/html`).

### 1.4 Files

- Names: `YYYY-MM-DD_HH-MM-SS__<F>_<temp>_<exp>s__<NNNN>.fits` (NINA). Older dark/bias files use a single underscore.
- **FITS headers are rich** (NINA 3.2) and are the source of truth: `IMAGETYP, FILTER, EXPOSURE, DATE-OBS/DATE-LOC, MJD-OBS, GAIN, OFFSET, READOUTM, XBINNING, SET-TEMP, CCD-TEMP, INSTRUME, TELESCOP, FOCALLEN, XPIXSZ, RA/DEC, OBJECT, OBJCTRA/DEC, OBJCTROT, CENTALT/AZ, AIRMASS, PIERSIDE, FOCPOS, FOCTEMP, HUMIDITY, DEWPOINT, AMBTEMP, WINDSPD, CLOUDCVR, SITELAT/LONG`.
- Current rig: RedCat 51 (250 mm) + QHY miniCam8M (IMX585, 2.9 µm, gain 9 / offset 100, "Linearity HDR"), filters **L R G B H(a) O S**, set temperature −10 °C, subs 120 s / 300 s.

### 1.5 Inconsistencies the indexer must tolerate (and the organizer may offer to fix)

1. **Year level missing**: `NGC 7331 (Caldwell 30)` puts `.SessionData` etc. directly under the target.
2. **Three session-folder naming schemes**, plus `[GARBAGE]` / `.OLDCAMERA` prefixes used as flags.
3. **Three inner layouts** (old flat layout, NINA object sub-folder, mosaic panels).
4. **Filter aliases**: `H` vs `Ha` (M 31), and `O`/`S` vs `OIII`/`SII` in astro-eval.
5. **Night date ≠ file date**: `NIGHT_2026-09-18` contains files stamped `2026-09-19_00-14`. A session is keyed by the *evening* date (local noon → noon).
6. `.Calibration` under a target mixes dated sub-folders with undated files (IC 5070: old-camera flats at `.Calibration\H\` directly).
7. Processing intermediates (`1.`–`7.`, `WBPP*`, `hjk`) live alongside raw data and are large.

**Design consequence:** the indexer classifies by **FITS header first, path second**, and every path rule is a configurable pattern, not a hard-coded layout.

---

## 2. Architecture

```
 BROWSER ──► AstroDashboard UI (Next.js, :3006) ──► Engine (FastAPI, :<next-port>)
                     │                                  │  ├─ indexer  → SQLite cache (headers, sessions, results)
                     │                                  │  ├─ diagnostics jobs (astro-eval + new checks)
                     │                                  │  ├─ organizer (dry-run plans → approved moves, journaled)
                     │                                  │  └─ observatory ──► NINA :1888 (REST + websocket), PHD2 :4400 (JSON-RPC)
                     └── SNR + ephemeris run in the browser/server (vendored TS from AstroPlanner)
```

- **UI — `web/`**: Next.js 15 App Router, React 19, CSS Modules, Industry tokens, `lucide-react` icons, `astronomy-engine`, `react-leaflet` for the sites map. Same conventions as AstroBlog/AstroPlanner, so the code reads the same.
- **Engine — `engine/`**: Python 3.12, `uv`, FastAPI, `astropy`, `numpy`, `sqlite3`. It depends on **astro-eval through a `uv` path source** (`../AstroSubQualityEvaluation`, editable), the same way AstroController already does.
- **Vendoring vs. linking**: AstroPlanner's `src/lib/snr/*`, `ephemeris.ts` and `coordinates.ts` are **vendored once** with provenance headers, following AstroPlanner's own self-containment rule. astro-eval stays a *package dependency* because it's a library with tests, and copying it would fork it.
- **Ports**: UI `3006` (from `next-port`), engine from the next `next-port` call. Both go in `project-dashboard.json`, then run `project_registry.py validate`.
- **Config**: `astrodashboard.toml` (`data_root = "G:/Astro"`, path patterns, filter aliases, sites, astro-eval overrides; NINA/PHD2 addresses and the advisor model are in Settings). Secrets come only from environment variables.

### 2.1 Data safety — enforced in code, not by convention

- The engine opens `G:\Astro` **read-only** everywhere except a single module, `organizer/apply.py`.
- `apply.py` only accepts a **plan ID** that the user approved in the UI. The plan is a list of `move(src, dst)`, `mkdir(dst)`, `rmdir_empty(dir)` and `trash(src)` operations. There is **no permanent-delete operation in the type system**:
  - `rmdir_empty` refuses any directory that still contains anything.
  - `trash` moves the item to `G:\Astro\.Trash\<plan-id>\`, keeping its original relative path, so it can be restored exactly.
  - Emptying `.Trash` is left to you, outside the dashboard. The UI shows its size and contents.
- Before applying, it re-checks that every `src` exists unchanged (size + mtime) and that no `dst` exists, so nothing is ever overwritten. Same-volume moves only, which keeps them atomic renames.
- Every applied operation is appended to `G:\Astro\.Dashboard\journal.jsonl`, and each plan has an **Undo** that generates the inverse plan (which also goes through approval).
- The engine writes its own files (cache DB, diagnostic results, thumbnails) to `AstroDashboard\var\`, **not** into `G:\Astro`. The one exception is the journal, so the move history stays with the data.
- Tests use a synthetic fixture tree. CI-style tests assert that no code path outside `apply.py` calls `os.remove`, `unlink`, `rmtree`, `rename` or `replace`.

---

## 3. Section 1 — Library (data organization)

### 3.1 Indexer

- Walks `data_root`, reads **headers only** (first 2880–28800 bytes; XISF via its XML header), and caches results in SQLite keyed by `(path, size, mtime)`. Rescans are incremental, so a re-scan after a new night takes seconds.
- Builds this model:
  `CatalogTarget → Revision → Session(night) → [Panel] → FrameGroup(type, filter, exposure, gain, offset, readout, bin, set-temp) → Frame`
- Resolves targets from folder names (`<ID> (<Name>)`), cross-checked against the `OBJECT` header. NINA object names like "Soul Nebula" map to the folder target.
- Exclusions: `_REJECTED` / quarantine folders → rejected; session folders not matching `NIGHT_<date>` (including the legacy `[GARBAGE]` / `.OLDCAMERA` ones until they are removed) → non-conforming. Both are counted separately and never mixed into the totals. Equipment changes are detected from `INSTRUME` / `TELESCOP` / `FOCALLEN`.
- Classifies non-raw folders (`WBPP*`, `.Finished`, legacy `1.`–`7.`) as **processing artefacts**. They aren't counted as data, but they're used for a processing-status badge and disk usage.
- File watcher (optional): rescans a session when new files appear.

### 3.2 Views

**Targets overview** — one row per target: thumbnail (from `.Finished` or a quick stretch), total integration, a per-filter bar (hours), number of nights, last session date, status badge (`Acquiring → WBPP done → Finished`, from `target.json` or inferred from the `WBPP\` and `.Finished\` contents), and completeness warnings. Sortable and filterable (by season, by filter set — SHO / LRGB / HOO — or "needs flats").

**Target page**

- **Integration summary per filter**: frames, hours, frames rejected/quarantined, and *goal vs. actual* (goals come from Planning, see §6.4).
- **Session matrix**: rows are nights, columns are filters. Each cell shows `N × exp` plus a **flat status dot** (✓ matching flats / ⚠ flats from another night / ✗ none) and a **dark status dot** (matching master dark in the library at that temp × exposure × gain × offset × readout).
- **Mosaic panels** as a sub-dimension (M 31 Panels 1–3) with per-panel totals.
- **Calibration readiness checklist**: every light group has flats with matching filter + binning, flats taken at the same focuser position and rotation, matching master darks and bias/dark-flats, and no camera-setting mixes. This reuses `astro_eval.session.group_session` / `calibration_summary` logic and extends it across nights.
- Night-conditions strip from headers: median humidity, dew-point margin, ambient temperature, wind, airmass range, moon illumination/separation (computed).

**Calibration library page** — the global `.Calibration` library as a matrix (temp × exposure × gain/offset/readout), with master age in days and the number of frames each master was built from. It **flags light groups with no matching master dark**. ("You shot 180 s at −10 °C on 2026-09-22; there is no 180 s master.")

**Organizer**

- Lint report of the inconsistencies in §1.5, per target.
- Proposes a **canonical layout** (below). It's always shown as a diff/dry-run, and you apply it per target or per session after approval.
- **Ingest**: automatic, from `.Download\` during acquisition. See §5.1.

#### Canonical layout (decided 2026-10-08)

**No year level. A *revision* of a target is its own target folder.** A revision is one acquisition + processing campaign: a reshoot in a later year, new equipment, or a fresh start for any other reason.

```
G:\Astro\
├── M 31 (Andromeda Galaxy)\                  ← revision 1 (no suffix)
│   ├── target.json                           ← revision metadata (see below)
│   ├── .SessionData\
│   │   ├── NIGHT_2026-08-20\
│   │   │   ├── M31_Panel_1\LIGHT\<F>\*.fits  ← mosaic: one <ID>_Panel_<n> per panel
│   │   │   ├── M31_Panel_2\LIGHT\<F>\*.fits
│   │   │   └── FLAT\<F>\*.fits               ← flats belong to the night, shared by all panels
│   │   └── NIGHT_2026-08-21\…
│   ├── .Calibration\  .Finished\  WBPP\     (+ legacy 1.–7. folders only where they hold files)
│   └── *.pxiproject
├── M 31 (Andromeda Galaxy) R2\               ← revision 2, e.g. a 2027 reshoot
│   └── … same structure …
└── NGC 7331 (Caldwell 30)\                   ← single-pane target
    └── .SessionData\NIGHT_2026-09-18\
        ├── LIGHT\<F>\*.fits                  ← no PANEL level when not a mosaic
        └── FLAT\<F>\*.fits
```

Rules:

- **Session folder = `NIGHT_<YYYY-MM-DD>`** (evening date), used for *every* session. WBPP grouping keywords are a single fixed name, so `NIGHT_` and `SESSION_` should not be mixed. `NIGHT_` is already the majority in the data. Use `NIGHT` as the WBPP keyword so that each night's lights calibrate with that night's flats.
- **Mosaic panel folder = `<ID>_Panel_<n>`** (catalog ID without spaces, e.g. `M31_Panel_1`; bare `PANEL_<n>` folders are renamed), directly under the night. Use `Panel` as a second WBPP keyword so that panels are calibrated and integrated separately. This replaces NINA's `M 31 Panel 1` object folder.
- **Flats**: `FLAT\<F>` directly under the night, replacing NINA's `FlatWizard\FLAT\<F>`.
- **Revision suffix**: none for revision 1, then ` R2`, ` R3`… appended to the folder name. Square brackets and extra parentheses are avoided on purpose: `[…]` is a wildcard in PowerShell paths, and parentheses already break `cmd.exe` scripts (see the AstroIntegration README).
- **`target.json`** at the revision root:
  ```json
  { "catalog_id": "M 31", "name": "Andromeda Galaxy", "revision": 1,
    "status": "acquiring | processing | finished",
    "mosaic": { "panels": 3 }, "goals": { "H": { "mode": "snr", "snr": 30 }, "O": { "mode": "hours", "hours": 15 } }, "notes": "" }
  ```
  When it's missing, the indexer infers everything from the folder name and headers. The dashboard writes it only as part of an approved plan.
- WBPP parses `NIGHT_2026-08-20` correctly as a keyword value (confirmed by you), so dates keep their hyphens.
- **Processing folders**: new revisions get **no `1.`–`7.` folders**. You now rely on WBPP, which writes its own `WBPP\` folder. Existing `1.`–`7.` folders are **kept when they contain files**; empty ones may be removed as part of an approved plan.
- **No session-flag convention.** A folder under `.SessionData` that doesn't match `NIGHT_<YYYY-MM-DD>` is reported as non-conforming and excluded from the totals until it's fixed.

How the dashboard uses revisions:

- The Library groups revisions under one **catalog target** (same `catalog_id`). The overview shows one row per target, which expands to its revisions. Totals and goals are always **per revision**, because data is never mixed across revisions.
- Planning's history ("you've already shot this") looks across all revisions of a catalog target.
- The indexer **warns when a revision mixes equipment** (`INSTRUME` / `TELESCOP` / `FOCALLEN` change) and suggests splitting it into a new revision. The old-camera sessions in IC 5070 and NGC 281 are examples.
- A **"Start new revision"** action creates `<Target> R<n+1>\` with an empty structure and `target.json`. It only creates folders and never moves data.

Migration of the existing tree (organizer plan, applied per target after approval):

| Today | Becomes |
|---|---|
| `<Target>\<YEAR>\…` | `<Target>\…` (year level flattened; each target currently holds a single year, so it becomes revision 1) |
| `2025-09-26`, `SESSION_2025-08-08` | `NIGHT_2025-09-26`, `NIGHT_2025-08-08` |
| `NIGHT_x\<NINA object>\LIGHT\` | `NIGHT_x\LIGHT\` |
| `NIGHT_x\M 31 Panel 1\LIGHT\` | `NIGHT_x\M31_Panel_1\LIGHT\` |
| `NIGHT_x\FlatWizard\FLAT\` | `NIGHT_x\FLAT\` |
| `.OLDCAMERA-…` and `[GARBAGE] …` sessions | **removed** (legacy, confirmed unused): IC 1848 `[GARBAGE] 2025-12-27`, `[GARBAGE] 2026-01-04`; IC 5070 `.OLDCAMERA-2025-07-26`; NGC 281 `.OLDCAMERA-SESSION_2025-08-01`. Any other match the scan finds is added to the list. |
| Empty `1.`–`7.` folders | removed; non-empty ones are kept |

PixInsight project files reference the old paths. The organizer re-points them as part of the plan (§3.3, as built).

### 3.3 Organizer as built (Phase 6, 2026-10-09)

Library → **Organizer** (`/library/organizer`). The machinery follows §2.1, with these decisions:

- **Plans are generated against a virtual copy of the target's tree.** Every operation sees the effect of the ones before it. A folder moves with a single rename when its destination is free, and is merged file by file when it isn't. A clash blocks the whole plan ("blocked"); nothing is ever overwritten.
- **The executor is `organizer/apply.py`.** It and `ingest/fileops.py` are the only modules that change the data root, and `test_safety.py` and `test_organizer.py` enforce it.
  - It refuses paths outside the root, into `.Download` or the global `.Calibration`, and trash that isn't in `.Trash\<this plan>`.
  - It re-checks every source (files: size and mtime) and destination just before each step.
  - It stops at the first problem and journals every step to `G:\Astro\.Dashboard\journal.jsonl`.
- **Approval:** a plan is a draft until you type its id. When a plan has warnings (e.g. projects whose seal PixInsight will report as edited) you also tick an acknowledgement. Applying holds the ingest lock, so auto-ingest never moves frames into a tree being reorganized.
- **Undo** is the inverse of what actually ran, newest step first. It is a new draft that needs its own approval.
- **Stored Diagnostics follow the frames.** Applying a plan (or its undo) rewrites the stored frame paths and renamed session keys in the dashboard database, so earlier analyses, previews and the learned horizon keep working.
- **"New revision…"** creates `<Target> R<n+1>\.SessionData` and a `target.json` (create-only). It moves nothing.
- **"New target"** (2026-10-12) creates revision 1 of a target the library doesn't have yet: `<ID> (<Name>)\` with `.SessionData\`, `.Calibration\`, `.Finished\` and a `target.json` (`status: acquiring`, plus `mosaic.panels` when more than 1). Like every organizer change it is a draft plan, approved by typing its id. The catalog id is looked up in the vendored catalog to fill in the canonical designation and common name. Names with `< > : " / \ | ? * ( ) [ ]`, or ending in ` R<n>`, are refused, and so is a catalog id already in the library, which needs "New revision…" instead.
- **Dry run on the real tree (2026-10-09, read-only):** no conflicts in any of the 14 targets. The legacy sessions found are exactly the four listed above.
- **PixInsight projects are re-pointed (2026-10-10).** A plan's last steps (`repoint`, `organizer/pxi.py`) rewrite the paths inside each `<target>\*.pxiproject\project.xosm`:
  - paths follow the plan's moves;
  - paths under a former data root (`E:/Photo/Astro/<existing target or .Calibration>/…`) move to `G:/Astro`;
  - paths into trashed sessions, unrelated drives (`C:/astro/ALIGNED`, `F:/Astrophoto/MARS Database`) and the image blobs in `project.data` are untouched;
  - the original file is moved to `.Dashboardackups\<plan>\…`, and undo restores it byte for byte.
- **Tested with PixInsight 1.9 (2026-10-10)** on copies, including one that saves its images: an edited *sealed* project opens with a "seal has been edited" warning and uses the new paths and images. Removing the seal only changes the warning, so the seal is left as it is; saving the project once in PixInsight reseals it. A project saved after the plan was prepared is refused (size, mtime and sha1 re-checked).
- **Dry run, real tree (2026-10-10):** 15 projects in 11 targets would be re-pointed, about 17,000 references in all; 11 of them still point at the old `E:/Photo/Astro` root today. Many references point to intermediate files you deleted after processing; those stay as they are. The shared projects in `.PixInsight\` are not touched.
- **Tested** on the synthetic fixture and on a scratch copy: apply, then undo restores the exact tree. No file disappears (trash is a move), and Library totals are identical after a migration.

**Not built:** a "quarantine this frame" action from Diagnostics, and offering `.Download` darks/biases to the calibration library. Both would be organizer plans. Neither is needed for the migration.

---

## 4. Section 2 — Diagnostics

Run on demand per session (or per target = every session) as **background jobs** with progress in the UI. Results are stored in the engine DB and are viewable without re-running.

### 4.1 Reused from astro-eval (`AstroSubQualityEvaluation`)

FWHM / wFWHM, eccentricity, star count, PSFSignalWeight, SNR weight, background level and noise, **background gradient** (`background._compute_background_gradient`), **satellite/plane trails** (`trail_detection`), **transparency** (relative stellar photometry, `transparency.compute_session_transparency`), composite score and rejection, and **flat-set checks** (exposure level, clipping, differenced noise, master-flat SNR, flat ↔ light focuser offset, settings mismatch).

The dashboard calls the library API (`compute_metrics`, `scan_session`, `group_session`, `flat_set_summary`, `calibration_summary`) directly instead of shelling out to the CLI, and renders its own charts. astro-eval's HTML report stays available as a "full report" link.

### 4.2 New checks (implemented in the engine; could be upstreamed to astro-eval later)

| Check | Method | Flags |
|---|---|---|
| **Obstruction** (tree, roof, dew shield, cable) | Star-density and background map on an 8×8 tile grid, compared with the session's median-reference map. A region with persistently low star density and changed background → obstruction. Track it over time to show *when* it began (e.g. the target sinking behind the tree line, correlated with `CENTALT/CENTAZ`). | Frames with an obstructed area, plus an **alt/az horizon mask** learned across sessions |
| **Twilight / dawn gradient** | Compute sun altitude per frame (`DATE-OBS` + site). Fit background level vs. time and flag frames once the sun is above −18° (astronomical) or −12°, or when the background slope accelerates. | "Dawn creep" frames at the end of the night |
| **Moon gradient** | Moon altitude, illumination and separation per frame, plus the gradient direction compared with the moon's position angle. | Gradient explained by the moon vs. unexplained |
| **Clouds / haze** | Transparency drop + star-count drop + background rise in the same frames (also `CLOUDCVR` if present). | Cloud episodes on a time axis |
| **Dew** | FWHM growth over the night together with `AMBTEMP − DEWPOINT` margin < 2 °C. | Possible dew |
| **Focus drift** | FWHM vs. `FOCTEMP` / time, with autofocus events (jumps in `FOCPOS`). | "Refocus needed" intervals |
| **Tracking** | Eccentricity, elongation axis (RA vs. Dec), and meridian-flip boundary (`PIERSIDE`). | Bad-guiding stretches; links to the Live guiding history when available |
| **Flat ↔ light mismatch** | Divide a median-stacked, normalized light by the master flat. Residual dust donuts or vignetting asymmetry show flats taken after a camera rotation / refocus / dust moved. | "Flat doesn't correct this night's lights" |
| **Flat quality** | Illumination uniformity, ADU vs. well depth (target ~40–60 %), panel banding/flicker (row/column stats), and light leaks (flat-dark comparison when available). | Bad flat sets |
| **Mispointing** | Plate-solve-free check: frame-to-frame star-pattern registration offset relative to the session reference (astro-eval's `_register`). Large offsets → mispointed frame, which is what M 31 needed a quarantine folder for. | Candidates for quarantine |

### 4.3 Output

- **Session timeline** (x = local time): background, FWHM, stars, transparency, plus bands for twilight, moon-up and meridian flip. Frames are dots coloured by verdict, and clicking one opens a stretched preview with an 8×8 tile overlay.
- **Per-frame table** with verdict and reasons, and a "**Quarantine selected**" action. That action produces an organizer plan (move to `_REJECTED`), so it still goes through approval.
- **Session score card**: usable hours per filter after rejection. This number feeds the Library's integration totals as "good hours".
- Optional LLM narrative, reusing `astro_eval.analysis` (off by default, key from env).

### 4.4 Framing across nights (2026-10-20)

Is each night framed like the one before it, as far as dithering explains? `framing.py` measures, `api_framing.py` serves (`/api/framing/revisions/{rev}`, `/api/framing/sessions/{rev}/{night}`, and `framing` in `/api/live/state`).

- **Stars, not headers.** Each light is reduced to its 150 brightest stars (sep on 2×2-binned pixels, positions in sensor pixels; about 0.3 s a frame, limited by the disk), kept in the dashboard database (`frame_stars`, by path and size). Two frames are matched at any rotation: a translation vote at 0° and 180°, else a vote on the angle between star pairs of equal length, then a similarity fit. Meridian flips, a turned camera and different filters all match.
- **A night's framing** is the median pose (centre, and rotation folded into ±90°) of its lights relative to one of them. Its **spread** is the 90th percentile of the lights' distances from that median: dither and drift together. Dithering only shifts the frame, so the night's **dither** is the same percentile of the lights' shifts alone, and a change of rotation through the night is drift (a steady turn: field rotation from the polar alignment) or the framing being redone (a step: a meridian flip, a re-centring). Smoothing over neighbouring frames cannot tell a slow drift from the dither: NINA/PHD2 dithers wander (on IC 405, a jump of ~11 px every 6 frames, within ±25 px), so translation and rotation are what is kept apart. A light more than 150 px and 4× the typical distance away (the mispointing rule of §4.2) was framed differently and counts in none of them.
- **The distance is the largest corner shift**, so a shift and a rotation are judged in the same pixels, the ones the stack crops. A night against the previous night of the same revision and mosaic panel (a night without usable stars is skipped over): ✓ within the larger of the two spreads (never under 10 px), ⚠ within twice that, ✗ beyond. A single frame gets its own night's spread on top, since it dithers around that night's median; Live, with one frame of tonight, uses twice the previous night's spread.
- **Where it shows.** Library → target → Sessions: a *Framing* column (✓/⚠/✗ and the corner shift, one mark per mosaic panel, the numbers on hover), from a sample of 12 lights per night measured in the background after each scan. Diagnostics: a *Framing* block on the Summary (the verdict, and the night's dither against its change of rotation) and a *Framing* column per frame, from every light of the night (measured on first view, or as the last phase of an analysis); *Through the night* plots three Framing metrics with their reference lines: each light against the night's framing (shift and rotation, against the dither line), its rotation in px at the corners (against ± the dither: field rotation shows as a slope) and against the previous night (against "aligned up to"). Live: a line under the last image, for the newest light on disk, sent to the revision auto-ingest would send it to (its folder once ingested, else tonight's choice, the ingest plan, or NINA's object matched to a target).
- **On the real library (2026-10-20):** IC 405 NIGHT_2026-10-19 (100 lights): spread 39 px, dither 32 px, rotation changed 1.2° (47 px at the corners: a step across a gap in the frames at about 22:30, then a steady turn of field rotation until dawn, with a small step at the 04:25 flip), so 39 of the 100 lights sit beyond the dither line; NIGHT_2026-10-17: dither 45 px, rotation 0.12°, only dithering. Spreads within a night 15–60 px; nights re-centred 10–50 px apart. Flagged ✗: IC 405's 2026 nights against 2025 (145 px, 1.6°), NGC 6888 (camera turned 25° on 07-07, back on 07-12), NGC 7635 09-14 (224 px), Sh2-114 08-04 (1.6°), IC 63 08-31, IC 1805 09-29 and NGC 281 08-10 (60–85 px).

---

## 5. Section 3 — Live

*(Original design, superseded by §5.3: Live now talks to NINA and PHD2 directly.)* A dashboard-styled view over AstroController (which must be running; otherwise the page shows a clear "AstroController offline at :3005" state).

- **Now**: sequence step, target, filter, exposure progress, last frame preview (`/api/frame/latest.png`), and the guide star (`/api/guide-star.png`).
- **Guiding**: RA/Dec/total RMS in arcsec as a scrolling chart, with dithers, settles and star-loss markers. Shows the advisor's last decision.
- **Acquisition**: per-frame HFR, star count and saturation for tonight, frames per filter vs. the plan, and the **projected end-of-night total** (with the remaining dark time computed from ephemeris).
- **Conditions**: weather, moon, dew-point margin, and target altitude now and at sequence end.
- **Tonight's diagnostics**: as frames land in the image share, the engine runs the cheap diagnostics from §4.2 (obstruction, dawn, clouds) incrementally and raises alerts.
- **Controls**: links out to AstroController for anything that moves hardware. The dashboard stays read-only by default; a later phase can proxy the same buttons, with AstroController's token auth.

Transport: the engine proxies `GET /api/state` and `/api/stream` (SSE) from AstroController and adds the token header server-side, so the token never reaches the browser.

### 5.1 Auto-ingest from `.Download` (decided 2026-10-08)

During acquisition, NINA on the observatory PC writes over SMB into `G:\Astro\.Download\<YYYY-MM-DD>\` (a bare date, no `NIGHT_` prefix). Inside it is the same layout as a night folder in `.SessionData` (object folders, `FlatWizard\FLAT\…`). Ingest adds the `NIGHT_` prefix when it creates the destination. A `NIGHT_<date>` folder in `.Download` is accepted too. The engine runs on the machine that owns `G:\`. It watches `.Download\` and **automatically moves finished frames to the right target folder** under the canonical layout (§3.2).

This is the **one standing exception** to "every move needs approval". You approve it once, by switching auto-ingest on, and it is tightly bounded:

- **Same rules as `apply.py`:** moves only, same volume (G: → G:, so each move is an atomic rename), never overwrites, never deletes. Every move is journaled, and each night can be undone (undo generates the inverse plan).
- **Only from `.Download\` into a revision's `.SessionData\NIGHT_<date>\`.** Nothing else in the tree is ever touched automatically.
- **Three modes**, selected in the UI: `Off` · `Dry-run` (shows what it *would* move, live) · `Auto`. The default is `Dry-run`, and the first real night should be watched in dry-run before switching to `Auto`.
- **Runs inside the engine**, independent of whether the Live page is open. On engine start it catches up on anything left in `.Download\`.

**When a file is ready to move.** NINA may still be writing it over SMB, so a file moves only when all of these hold:

1. Its size and mtime have been stable for ≥ 30 s.
2. The FITS file is complete: the header ends in `END`, and the file size equals the header length + `NAXIS1 × NAXIS2 × |BITPIX|/8`, padded to 2880 bytes.
3. **It is not the newest LIGHT frame of the night.** AstroController's frame preview shows the newest file under its `[images].share_path` (= `.Download\`), so the latest frame stays in place until a newer one arrives, or until the night is closed (see below). This keeps the Live preview working.

**Where a file goes:**

| From `.Download\<YYYY-MM-DD>\…` | Resolved by | To `<Revision>\.SessionData\NIGHT_<date>\…` |
|---|---|---|
| `<NINA object>\LIGHT\<F>\x.fits` | `OBJECT` header → target mapping | `LIGHT\<F>\x.fits` |
| `M 31 Panel 2\LIGHT\<F>\x.fits` | object + `Panel (\d+)` pattern | `M31_Panel_2\LIGHT\<F>\x.fits` |
| `FlatWizard\FLAT\<F>\x.fits` | the night's target(s) | `FLAT\<F>\x.fits` |
| `Snapshot\`, PHD2 guide images, DARK/BIAS | — | **left in place**. DARK/BIAS are offered to the calibration library as a normal approved plan. |

- **Night**: computed from each frame's `DATE-LOC` (local noon to noon → the *evening* date). The `.Download` folder date is only a cross-check. Depending on NINA's path pattern (`$$DATE$$` vs `$$DATEMINUS12$$`), frames shot after midnight may land in a folder dated the next day. Using the header keeps a whole night in one `NIGHT_<evening date>` folder either way. If the folder and header dates disagree by more than a day, the frame is held and you get an alert.
- **Filter alias normalization** follows the config (e.g. `Ha` → `H`) for the *folder name only*. File names and headers are never rewritten.
- **Tonight's target** chosen in Live, when set, wins over the mapping for that night (§5.3).
- **Copies re-synced into `.Download`** (found 2026-10-15): NINA's *Robocopy Start* step keeps `.Download` in step with the observatory PC's image folder, so every frame auto-ingest moves comes back as a copy (original timestamps). A file whose destination already exists with the same size and timestamp is held as **already ingested** (not waiting, not a conflict). Each night offers *Remove N copies already ingested…* (confirmation; `fileops.remove_duplicate` compares each byte for byte with the ingested frame and removes only identical ones; logged). Never automatic.
- **No night folder** (found 2026-10-14: NINA's file pattern wrote `.Download\IC 405\LIGHT\Ha\…`): frames lying directly under `.Download` are grouped by their header night, which stands in for the folder name (journal, undo, close). Empty-folder cleanup is offered only for real night folders.
- **Target mapping**: a table `NINA object name → catalog target`, e.g. `"Soul Nebula" → IC 1848`. It's pre-filled from the existing data, since every current session already pairs a NINA object folder with a target folder, and it's editable in the UI. Frames go to the target's **latest revision whose status is `acquiring`**.
- **Unknown object, or the latest revision is `finished`**: the frames **stay in `.Download\`**, and the Live page shows a prompt: map to an existing target, start a new revision, or create a new target. Once you choose, ingest continues automatically, and the choice is remembered.
- **Flats shared by several targets on one night**: flats are moved to the first target and **copied** to the others. A copy never removes anything. The Library notes "flats shared with <target>" so you can check that the rotation and focus match.
- **Conflicts** (destination file already exists, header/folder disagreement such as a `FILTER` header that differs from the folder): the file is held in place and raised as an alert. It is never guessed.

**Closing the night.** Once the sequence has ended (from NINA) or after local noon, the last held frame is moved. Directories left empty in `.Download\<YYYY-MM-DD>\` are then removed with `rmdir_empty`. Live then shows a night summary: frames ingested per target/filter, anything held back and why, and a link to run diagnostics on the new session.

**Tie-in with Live diagnostics.** The cheap checks in §4.2 (obstruction, dawn creep, clouds, mispointing) run on each frame as it is ingested. The results are on the Live page within a minute of the frame being saved.

### 5.2 As built (Phase 5, 2026-10-09)

**Live.** The engine reads AstroController's `/api/state` and its two preview images and serves them under `/api/live/*`, adding `ASTROCONTROLLER_TOKEN` from its own environment (never stored, never sent to the browser). The page polls every 3 s rather than relaying the SSE stream: simpler through the Next.js rewrite, and plenty for a page that changes once per guide step. It shows the running step, camera/mount/focuser/guider state, the latest frame, RA/Dec guiding, conditions and moon, and tonight's frames with AstroController's own flag notes. It is read-only; controls stay in AstroController. The address is in Settings → Live · Ingest.

**Auto-ingest** follows §5.1, with these decisions:

- `ingest/fileops.py` is the only module that changes the data root (`test_safety.py` allows it and nothing else; `test_ingest.py` checks that only the ingest service imports it). Every call re-checks its own paths: source under `.Download`, destination in a `.SessionData\NIGHT_` folder of the same volume, never overwriting.
- **Default mode is Dry-run**, and switching to Auto needs a confirmation in the UI. It has not been switched on.
- **Undo pauses the night.** Otherwise Auto re-ingests it on the next pass (found while testing). A paused night shows what would move and has a Resume button.
- **Empty `.Download` folders are never removed automatically.** Removing them is a deletion, so each night has a "Remove N empty folders…" button with a confirmation, and the API refuses without `confirm: true`. `os.rmdir` itself refuses a folder with anything in it.
- **Flat copies are not deleted by undo.** They are reported instead, because removing a copy is a deletion.
- **A night is closed** by your click, after the next local noon, or when NINA reports no running step. Until then the newest light stays for the preview.
- **The object → target table** is learned from the OBJECT headers already in the Library (e.g. "Bubble Nebula" → NGC 7635). Your own rows win and can name a specific revision, including a finished one.
- **Tested end to end** with a scratch data root (the throwaway fixture used while building), never `G:\`: dry-run plan, mapping prompt, Auto moves and the flat copy, close night, undo + pause, and cleanup.

**The download folder is a setting** (2026-10-21, Settings → Live · Ingest; `ingest/paths.py`): the folder NINA saves into over SMB, by default `<data root>\.Download`. Live's last image, the framing check and auto-ingest all read it. To keep ingest's guarantees it must be on the data root's drive (a frame only moves by a rename, never by a copy and a deletion; a UNC path or another drive is refused), and inside the data root only a dot folder directly under it (any other folder there is a target; `.Calibration`, `.Trash`, `.Dashboard` are taken); never the data root or a folder holding it. The settings API checks this and that the folder exists; `fileops` re-checks it on every operation, and a pass whose folder no longer qualifies plans nothing. The organizer now stays out of every top-level dot folder but its own `.Trash` and `.Dashboard`. Undo moves frames back into the folder in use, so it is best changed between nights.

**Not built (yet):** incremental per-frame diagnostics on ingest (the night's summary links to Diagnostics instead); offering DARK/BIAS frames to the calibration library (they stay in `.Download` for the organizer, Phase 6); push alerts for held files (they show on the Live page); and the real-night dry-run check, which is yours to do before switching Auto on.

### 5.3 Self-sufficient Live (2026-10-14)

AstroController is no longer in the loop. `engine/astrodash/observatory/` owns the connections (logic ported from AstroController, trimmed):

- **NINA** (`nina.py`): Advanced API REST for truth (equipment every 4 s, `/sequence/json` flattened into a step tree, image history), the `/v2/socket` websocket for change events (IMAGE-SAVE, flips, autofocus, sequence start/end). A websocket reconnect re-reads REST.
- **PHD2** (`phd2.py`): one TCP socket demultiplexing events and id-matched JSON-RPC; state seeded on every connect (app state, pixel scale, exposure, Dec mode, calibration, algorithm and parameters per axis).
- **Runtime** (`runtime.py`): asyncio tasks on the engine's loop, each reconnecting with backoff. Addresses come from Settings (`observatory`) and a change reconnects at once. Auto-ingest's night closing reads the sequence state from here.

**Live › Overview** shows only connected devices (one card each, key facts; more in Advanced mode), a warning when a device that was connected drops out, the sequence (target, running step, exposure *n/m*, countdown; the full tree in Advanced), the last image (the newest file in `.Download` when it is NINA's newest frame, else NINA's own JPEG), a guiding summary, and tonight's frames with HFR/star flags against the same filter's recent frames.

**Live › Guiding**: RMS tiles (about zero, over the last 5 clean minutes; dithers, settling, star loss, flips, autofocus and parameter changes are masked), the RA/Dec trace with masked spans and event markers, the guide star (PHD2 `get_star_image`), a scatter, a deterministic per-axis **diagnosis** from the shape of the error (lag-1, offset, idle, reversals → overcorrecting / deadband too wide / undercorrecting / chasing seeing / seeing-limited / healthy), the parameters (edit any numeric PHD2 parameter, guide exposure, Dec mode) and **Changes tonight**, where every change is measured 5 min before vs 5 min after (± 1.5 combined standard errors = no clear change; guide-star HFD moving > 20 % = void) and can be reverted. Guide/Stop/Pause/Dither and the shape table, pulse chart and calibration are Advanced-only.

**AI advisor** (off by default, toggled on the Guiding page): every N minutes of steady guiding (or on "Analyze now") it sends the numbers, the diagnosis, conditions (seeing from the guide-star HFD, SNR, altitude, pier side, NINA weather device), setup (algorithms, exposure, scale, Dec mode, calibration), the parameters with safe ranges and tonight's measured changes to the model, and gets at most two suggestions back. **It only suggests**: you click Apply. Suggestions are validated (whitelisted parameter the algorithm exposes, inside the range, step ≤ max step; far-out values dropped). The periodic run skips the model when nothing is actionable. An **Auto-apply** toggle (off by default) applies the top suggestion itself, one change at a time — never while the last change is being measured or a change that measured worse tonight — and puts an automatic change back when it measures WORSE. It may also suggest **switching an axis's algorithm** (RA: Hysteresis, Lowpass2, Resist Switch, Predictive PEC; Dec: Resist Switch, Lowpass2, Hysteresis), one switch per answer and only when the current algorithm's parameters cannot fix the problem. PHD2's server has no command for the switch (`set_algo_param` takes numbers only), so you make it in PHD2's Brain (Algorithms tab) and click **Done** on the suggestion in place of Apply. Done checks that PHD2 reports the new algorithm, then logs the switch as the AI's change and measures it from that moment, after a longer settle: 2 min, or 10 min for Predictive PEC, which has to learn the period. "Before" is the guiding up to the switch itself: the switch writes PHD2's profile, PHD2 sends ConfigurationChange, and the dashboard notes the moment (or, if it was not watching, it uses the guiding the advisor analysed). A switch that follows no suggestion is logged and measured as yours straight away. Auto-apply leaves switches to you. The Guiding parameters panel shows the algorithm as a read-only row per axis. Unlike AstroController there is no cross-night learning store — a deliberate cut; the change log in the dashboard DB keeps the evidence if that is wanted later. Providers: `ollama`, `ollama_cloud`, `openrouter`, `openai`, `anthropic`, called over plain HTTP; keys from `.env`.

**Sky monitor** (2026-10-20, replaces the sequence guard; off by default; toggle on Live › Overview's Sequence card, thresholds in Settings → Live · Ingest; `observatory/sky.py`, `observatory/alpaca.py`): the dashboard tells NINA whether the sky is clear and **never drives NINA**. The old guard stopped and restarted the sequence through the Advanced API and took its own test frames meanwhile; that fought whatever you were doing in NINA, and a stop left the end-of-sequence instructions unrun, worst when the scope pointed at a building and never "cleared". Now the engine serves an **ASCOM Alpaca SafetyMonitor**, "AstroDashboard sky monitor" (ISafetyMonitorV3, device 0), on its own port (`[alpaca] port`, 3008, every interface since NINA runs on the observatory PC; only this device is served there), in its own thread and event loop, with Alpaca discovery on UDP 32227 (`[alpaca] discovery`). NINA connects to it in Equipment › Safety Monitor, and the sequence decides what unsafe means. In the target's imaging loop (*Loop Until Time* / altitude, with *Restore Guiding* and *Center After Drift*): the exposures and their dither in a Sequential Instruction Set with *Loop While Safe*, then a second one with *Loop While Unsafe* holding a 1 min *Wait for Time Span*. NINA interrupts the exposure and does not save it. Short waits rather than one *Wait until Safe*, because NINA evaluates triggers only between instructions: a long wait would hold off the meridian flip. The loop's own condition ends a wait that lasts too long; guiding stopped at the target's end keeps its evidence from the next target; the end of the sequence runs as usual. IsSafe is false while the device is not connected, as ASCOM asks. **Unsafe**, from PHD2's events of the current guiding run: the star lost (`StarLost` until the next `GuideStep`) for at least *Unsafe at* (75 %) of the last *Window* (60 s) — a share, so broken cloud counts — or its brightness (median `StarMass` over the window) under *Dim below* (50 %) of its clear-sky brightness. That is the 80th percentile of the run's star mass over the last 30 min while the monitor said clear, once 2 min of it exist; it is frozen while unsafe and restarts when the guide exposure changes. Thin cloud and haze dim the star long before PHD2 loses it. **Clear again**: *Clear after* (120 s) of guiding without losing the star, at *Dim below* + 15 points of the clear-sky brightness or more over that time (hysteresis). **Never unsafe**: no evidence is clear — a run ends at `GuidingStopped`, `Paused`, `StartCalibration`, `StarSelected` or PHD2 going away, and the next run starts from nothing, so NINA is never held on old evidence; the mount below the horizon profile of the site nearest its own coordinates (else the default site; Settings › Sites, the profile only, not the site's minimum altitude) is an obstruction, not cloud; unsafe for *Give up after* (120 min) without a break is more likely an obstruction the profile lacks, and *Report clear* on the Live bar is you saying so: both say clear until the next guiding run. PHD2 must keep guiding while NINA waits (its star is how the sky is seen to clear). The Live bar shows the verdict and why, the Sequence card whether NINA reads it (its last IsSafe question), the Guiding graph *clouds* / *clear* markers. The simulator's NINA safety monitor is the sky monitor and its sequence saves nothing while unsafe; `/fake-nina/v2/api/sim/cloud?seconds=N` loses the star, `/sim/haze?seconds=N&pct=P` dims it. The guard's NINA calls (sequence stop/start, filter change, capture, capture statistics, abort) are gone; the old `guard` setting is retired.

**Tonight's target** (Live › Overview): pick the revision tonight's frames belong to, and auto-ingest sends every light of the night in progress (noon to noon, from the frames' own DATE-LOC, so after-midnight frames count) to that revision's `.SessionData\NIGHT_<date>\LIGHT\<filter>\`, whatever NINA calls the object; flats follow as before. It beats the OBJECT mapping, and one NINA object can be pointed elsewhere when a night has several. A revision that is not *acquiring* (finished, processed) is allowed only after a warning (the API refuses without `confirm: true`), with a pointer to the organizer's New revision. The card suggests the revision matching NINA's running target, lists tonight's objects with where each would go and why a frame is held, and offers to switch ingest to Auto (with the usual confirmation) since nothing moves in Dry-run. Choices are stored per night in the ingest state (last 14 nights); undo per night is unchanged.

**Following NINA** (default on, the card's first switch): NINA's target name comes from `/sequence/state` (each target container's `Target.TargetName`; `/sequence/json` lacks it), and it is also what NINA writes into OBJECT. Each OBJECT is matched to its library target — the mapping table first, then catalog id or name ignoring case, spaces, dashes and underscores — so a night with several targets sends each one's frames to its own revision (per object, so a target's last held frame never follows NINA to the next one). The newest acquiring revision wins. When every match is finished or processed, nothing is switched and the card says why, unless **"Also switch to finished targets"** is on (asks first): then the frames go to the newest finished revision, and in Auto mode, before its first frame moves, `fileops.set_status` writes `"status": "acquiring"` into that target folder's own `target.json` (only that key; other fields kept; written to a temp file and swapped in; nothing else in the data root). Every status change is logged on the card with **Mark finished again** (`POST /api/ingest/status`). Note that with the switch still on and NINA still on that target, it is reopened again at the next frame. Choosing a target yourself (every frame of the night) pauses following; "Follow NINA instead" resumes it.

**Simulator**: `astrodash-engine --fake-observatory` runs a fake PHD2 (real wire protocol, mount that responds to the parameters: RA starts overcorrecting, Dec with a deadband too wide) and a fake NINA mounted on the engine at `/fake-nina`. Nothing is written to disk.

**Dropped from the old page:** Open-Meteo weather and moon (NINA's weather device shows when connected; Planning has the forecast). Not ported: polar alignment (TPPA), sequence start/stop, the guide-camera full-frame dump.

---

## 6. Section 4 — Planning

### 6.1 Sites

Sites are managed on the **Settings → Sites** page (decided 2026-10-08) and selected in the Planner.

**Settings → Sites** (add / edit / duplicate / delete; at least one site must exist):

| Field | Notes |
|---|---|
| Name | e.g. "Home", "Mont-Mégantic" |
| Latitude / longitude | Typed, or pasted as `45.544, -73.538` or a Google Maps link; a small map preview confirms the point |
| Elevation (m) | Optional, used for refraction and extinction |
| Sky brightness | Enter **either** SQM (mag/arcsec²) **or** Bortle class; the other is shown converted. This value feeds the SNR sky model |
| Horizon | A minimum altitude (default 20°) plus an optional per-azimuth profile, edited as a polar plot. For Home the profile can be **learned from the obstruction diagnostics** (§4.2), e.g. the tree line hiding everything below 25° at azimuth 200–260°; you review the learned profile before it's saved |
| Default | One site is the default for the Planner |

**Sites map.** The Sites page opens on a map showing every site as a pin, with the site list beside it:

- **Library**: Leaflet (`react-leaflet`), with OpenStreetMap-based tiles. No API key is needed. Tiles are muted to match the Industry style. *As built:* CARTO's basemaps now return an "API key required" watermark, so the map uses the OpenStreetMap standard tiles, greyscaled by a CSS filter in the light theme and inverted in the dark/night themes, with the OSM attribution shown.
- **Pins**: square markers in the accent colour, matching the design system's square corners. The default site gets a distinct marker. Pin size or a small badge reflects how many nights were shot there.
- **Clicking a pin** opens a card with the name, coordinates, elevation, SQM/Bortle, horizon minimum, nights shot, last session date, and links to **Edit** and "**Plan here**" (opens the Planner with that site selected).
- **Placing and moving sites**: "Add site" lets you click on the map to drop a pin. In the edit form, the pin can be dragged to fine-tune the coordinates. The same map is the preview used when filling a site from a FITS frame (below).
- **Suggested sites** from "Suggest sites from my data" appear as hollow pins until you accept or dismiss them.
- **Optional layer: light pollution.** A toggleable overlay of artificial sky brightness helps when scouting for new dark sites. The tile source has to be checked for its terms of use before it's enabled. Candidates are the Falchi et al. 2016 World Atlas data or a VIIRS-derived layer. If no usable source is found, this layer is dropped. It's not essential.
- **Offline**: if the tiles can't load, the pins still render on a plain lat/long grid, so the page stays usable.

**Create or fill a site from a frame.** On the site form, a "**From a FITS/XISF frame…**" button reads a frame shot at that site and fills in the fields:

- **Picking the frame**: drop a file from disk, or pick any frame already in the Library. A dropped file is **not uploaded**: the browser reads only the header (the first ~30 KB, via `File.slice`), so a 17 MB sub costs nothing. XISF frames are read from their XML header's `FITSKeyword` entries.
- **Fields extracted**, accepting the common keyword variants:

  | Field | Keywords |
  |---|---|
  | Latitude | `SITELAT`, `LAT-OBS`, `OBSGEO-B` |
  | Longitude | `SITELONG`, `LONG-OBS`, `OBSGEO-L` |
  | Elevation | `SITEELEV`, `ALT-OBS`, `OBSGEO-H` |
  | Suggested name, date | `OBJECT`, `DATE-LOC` (shown for context) |

  Both decimal and sexagesimal (`45 32 39`) values are parsed.
- **Checks before filling**: the extracted values are shown as a **preview with the map pin**, and nothing is saved until you confirm. Things the preview catches:
  - An elevation of exactly `0` is treated as "not set". Your NINA headers currently write `SITEELEV = 0.0`, so you get an elevation estimated from the coordinates, with an option to type it in.
  - Some software writes longitude as west-positive. The map pin makes a flipped sign obvious, and a "flip sign" button fixes it.
  - If the coordinates are within 5 km of an existing site, the form offers to update that site instead of creating a duplicate.
- **Optional sky-brightness estimate**: the frame's median sky background is converted to an SQM-equivalent, using the camera gain in e⁻/ADU, the pixel scale, the exposure time and the filter bandwidth. This is the same inversion as AstroPlanner's Calibrate tab. The estimate is labelled **approximate**, and it is only offered when the conditions make it meaningful: a broadband or L frame, the sun below −18°, the moon down, and altitude above 40° (all computed from the header). Otherwise it explains why it was skipped. Several frames can be given, and their median is used.

**"Suggest sites from my data"** does the same in bulk. It clusters the `SITELAT/SITELONG` of every indexed session and proposes one site per cluster, with the number of nights shot there. You confirm each one before it's added.

- **Home is pre-filled** from the FITS headers (`SITELAT/SITELONG` 45.54°N, −73.54°), with SQM ~18 as an editable starting value.
- The timezone is derived from the coordinates, so night boundaries and local times are correct at every site.
- Sites are stored in the engine's database (`var\`), not in `G:\Astro`, and can be exported and imported as JSON.

**In the Planner:**

- A **site selector** sits in the Planner header and defaults to the default site; the last choice is remembered. Changing the site recomputes everything: darkness windows, altitudes against that site's horizon, moon impact, the sky background in the SNR model, the target ranking, the calendar, and SNR-derived goal hours (§6.4).
- **Compare sites**: for a chosen target and night, show usable hours and the hours needed to reach the goal at each site side by side. This answers whether the drive to the dark site is worth it for that target.

**Elsewhere:**

- Sessions are tagged with the **nearest known site** (within 5 km) from their `SITELAT/SITELONG` headers. The Library can then show where each night was shot, and Diagnostics uses that site's horizon and sky brightness.

### 6.2 "What to shoot" for a chosen date range and site

For each candidate target (your existing targets, NINA `Targets\*.json`, and a catalog of bright nebulae and galaxies vendored from AstroPlanner's `catalog/`):

- **Imaging time**: hours above the minimum altitude *and* above the horizon mask *during astronomical darkness*, tonight and summed over the next 30/60/90 days. Shown as a ranked list and a year-long "season strip" per target.
- **Moon-aware filter suggestion per night**: illumination, moon altitude during the target window, and angular separation, combined with the Krisciunas–Schaefer sky model from AstroPlanner's `sky.ts`. The result is a recommendation such as "Bright moon 15° away → shoot Hα; OIII/L are poor tonight", using the predicted per-filter SNR loss vs. a moonless night rather than a rule of thumb.
- **Score** = usable hours × predicted SNR rate × "need" (how far the target is from its goal; see 6.4) × your preference weights.
- **Calendar**: the next N nights with moon phase and the best target/filter per night.

### 6.3 SNR (from AstroPlanner)

SNR vs. integration curves per filter for the selected target, site and rig, plus hours to a target SNR, optimal sub length and read-noise penalty. These are the vendored `src/lib/snr/*` modules, pinned by a ported copy of AstroPlanner's `verify-snr` checks.

### 6.4 Goals — the bridge between Planning and Library

Goals are set **per revision, per filter**, and **each filter picks its own mode** (decided 2026-10-08):

- **Hours** — "I want 10 h of Hα". This is the manual override, for when the SNR model's answer isn't credible.
- **SNR** — "I want SNR 30 in OIII". The planner converts it to hours with the vendored SNR engine, and the hours are recomputed whenever the model inputs change (site, rig, calibration).
- **None** — the filter is tracked but has no goal, e.g. RGB just for star colour.

A target can mix modes, e.g. Hα and SII by SNR, OIII as a fixed 15 h, RGB with no goal.

```json
"goals": {
  "H": { "mode": "snr",   "snr": 30 },
  "O": { "mode": "hours", "hours": 15 },
  "S": { "mode": "snr",   "snr": 25 },
  "R": { "mode": "none" }
}
```

Both numbers are always shown side by side, whichever mode is chosen. An SNR goal displays its implied hours, and an hours goal displays the SNR it is predicted to reach. Switching a filter from SNR to hours **pre-fills the hours** with the current implied value (or a capped value; see below), so overriding is one edit.

**Sanity check on SNR-derived hours.** Some filters come out with impossible totals, typically because OIII/SII brightness is *derived* from Hα via class line ratios, or because the target is beam-diluted in the Hα survey (see the AstroPlanner README). The goal editor flags a filter when its implied hours are:

- more than a configurable cap (default 40 h per filter), **or**
- more than the usable dark hours the target has left this season at the selected site (from §6.2).

The flag offers "**switch to hours**" pre-filled with the cap. It also names the likely cause: estimated OIII/SII ratio, beam-diluted Hα, or the sky model.

**Fixing the model rather than overriding it.** Once a revision has data, the engine can **calibrate the SNR model from your own subs**. It measures the sky and target signal in electrons per second from a session's frames, which is what AstroPlanner's Calibrate tab does by hand. Calibrated filters are marked "measured" instead of "estimated", and their implied hours usually stop being unrealistic. That makes SNR mode trustworthy again for the next nights on that target.

**Where goals are used:**

- The Library shows progress bars per filter: actual *good* hours (after diagnostics rejection) against the goal hours, with the predicted SNR so far.
- Planning's "need" factor uses the remaining deficit in hours, whichever mode set it.
- Goals are stored in the revision's `target.json`, and are editable from the target page.

### 6.5 As built (Phase 4, 2026-10-09)

Where the build follows §6.1–6.4 it is not repeated here; these are the decisions and differences.

**Split of work.** The engine supplies what only it can see; the planner itself runs in the Next.js server on the vendored AstroPlanner modules, so pages arrive fully rendered (≈ 70 ms for 13 targets, 363 catalog ideas and 30 calendar nights).

- Engine (`astrodash/planning.py`, `halpha.py`, `api_planning.py`): sites, goals and settings in `var/astrodash.db`; header site parsing and clustering; rig detection from the headers; revision pointing (mean RA/Dec of the lights); the measured sky; the Hα survey measurement with a cached, rate-limited background prefetch.
- Web (`src/lib/snr`, `src/lib/astro` vendored unmodified with provenance headers; `src/lib/planning` new): fast night grids (sun/moon/sidereal time once per night, then trigonometry per target), per-filter evaluation, ranking, calendar, calibration. `scripts/verify-planner.ts` pins it to the vendored `ephemeris.ts` (alt/az within 0.6°, darkness within a minute, usable hours within 20 min) and the vendored sky model.

**Sky calibrated from your own subs, by photon transfer.** For each kept light of an analysed night, sky electrons = ((background − bias pedestal) / background noise)², independent of the camera gain. The pedestal is the median of raw biases at the light's settings (≈ 400 ADU in Linearity HDR). Comparing with the model at that moment (site SQM through the filter, plus moonlight) gives κ per filter and site; frames with the moon down are preferred. At Home: L κ 1.04 (≈ SQM 17.96), H 1.06, O 2.34, S 0.81. The same measurement gives the camera's e⁻/ADU as a by-product (≈ 0.70 in HDR, against the 0.35 preset). Target signal is still estimated (Hα survey or class typical, OIII/SII by class ratios); measuring it from subs is left for later.

**Two additions to the sky model** (stated in `skymodel.ts`): κ scales only the dark sky (light pollution is line-rich, moonlight is a continuum), and KS91's Rayleigh term is coloured as (550/λ)⁴, so the moon costs OIII and B more than Hα and SII.

**Scoring.** Value of a filter tonight = usable hours × efficiency (SNR² tonight ÷ same hours moonless) × need × (1 + (2·moon-down fraction − 1) × moon sensitivity). The last term is the opportunity cost: moonless hours go to the filters the moon hurts most, moonlit hours to the ones it does not. Need is the goal deficit; without goals, filters lagging behind the target's best-covered one are wanted more. Finished targets are hidden unless included. Ideas are weighted by brightness (hours to SNR 30) and framing (best at ~60 % of the field width), and exclude anything inside one of your fields.

**Goals are stored in the dashboard database**, not in `target.json`: writing into the data root is reserved for approved organizer plans (Phase 6 can export them). A `target.json` `goals` block is still read and shown until goals are saved in the dashboard. Goals are editable on the Planning target page and on the Library target page.

**Not built (yet):** elevation estimated from coordinates (a DEM lookup; the field says "type it in"), the light-pollution map layer, picking an individual Library frame for a site (the "Suggest sites from my data" clusters cover frames already in the Library), timezones per site (times show in the dashboard machine's local time; night boundaries are computed from the sun, so they are right at any site), and preference weights beyond the stage weighting.

### 6.6 Telescopius (2026-10-14)

Optional, with `TELESCOPIUS_API_KEY` in `.env` (API v2.2, non-commercial use only). `astrodash/telescopius.py` is the client, and `api_telescopius.py` serves `/api/telescopius/{status,object,lists,events}`. The key stays in the engine.

- **Object card** on the Library and Planning target pages: reference thumbnail, every catalogue ID and name, size, surface brightness, magnitude, PA, morphology, and a link to Telescopius. It is looked up by catalog ID, then by name. A found object is cached for good; an unknown name is retried after 30 days.
- **Your target lists** (cached 6 h) join the Planning idea pool. They are ranked like the catalog ideas, and shown in their own "From your Telescopius list" section even when out of season; targets already in the library are left out. A list target the vendored catalog knows uses the catalog entry; otherwise it is built from the list's position, size and PA, with an unknown object class.
- **Sky events** (`/events/highlights`, cached 6 h per site) appear on the Planning overview (next 30 days) and on each night page. Not shown: Moon phases, apogee/perigee, inferior conjunctions, perihelia, and occultations not visible from the site.
- **API limits:** 60 requests/minute, and a search costs 2, so calls are spaced 2.1 s apart. A failed call is not retried for 10 minutes, and the last good answer is served meanwhile. Results come under `page_results` (not the spec's `objects`). RA is in hours, sizes are in arcseconds (list `size_deg` in degrees), and event times are UTC.
- **Not used:** the yearly ephemeris (one point per day at midnight, worse than ours), the weather forecast (Astrospheric is the source; the Telescopius one could become a second opinion), equipment (the rig comes from the headers), news.

---

## 7. Design

Port AstroBlog's Industry system: `tokens.css` copied with a provenance header, Barlow + Barlow Condensed via `next/font`, and **square corners**. Cards are hairline line drawings with no fill, and registration-mark `+` glyphs sit at the corners of framed objects. Labels are uppercase mono with wide tracking. The steel-blue accent is used for chrome only, with `accent-700` for body-size text.

Additions the dashboard needs (to be kept in the same spirit):

- **Status palette** (ok / warn / bad / excluded), validated for contrast against `--color-bg`. Every status also gets a glyph, not colour alone.
- **Filter colours** (L, R, G, B, Hα, OIII, SII) as a fixed categorical set reused in every chart.
- **Dark theme** for Live and night use. A **red night mode** toggle is worth having at the scope.
- Charts: uPlot (already used by AstroController) for the time series, and plain SVG for the small matrix and bar elements.

Navigation: a top bar with **Library · Diagnostics · Live · Planning**, plus a global target search and a **Settings** icon.

**Settings** groups everything that's otherwise only in `astrodashboard.toml`. Values edited in the UI are stored in the engine's database and take precedence over the file:

- **Sites**: the site editor (§6.1).
- **Data**: data root (`G:\Astro`), filter aliases (`Ha` → `H`…), and the default rejection behaviour.
- **Auto-ingest**: mode (`Off` / `Dry-run` / `Auto`), and the NINA-object → target mapping table (§5.1).
- **Rig**: telescope, camera and filter set used by the SNR model (pre-filled from the headers).
- **Goals**: the cap for flagging unrealistic SNR-derived hours (§6.4).
- **Live**: NINA host/port, PHD2 host/instance, the AI advisor's model, interval and target RMS. API keys come from `.env` and are never shown or stored.

---

## 8. Project layout

```
AstroDashboard/
├── PLAN.md
├── project-dashboard.json        # two services: UI :3006, engine :<next-port>
├── astrodashboard.toml.example
├── web/                          # Next.js UI
│   ├── app/(library|diagnostics|live|planning)/…
│   ├── src/components/…  src/styles/tokens.css (ported)
│   └── src/lib/vendor/astroplanner/{snr,ephemeris.ts,coordinates.ts}
├── engine/                       # FastAPI + uv
│   ├── astrodash/
│   │   ├── config.py  db.py  api/
│   │   ├── index/{scanner,headers,layout_patterns,model}.py
│   │   ├── diagnostics/{runner,obstruction,twilight,moon,flats,mispoint}.py
│   │   ├── organizer/{lint,plan,apply,journal}.py   # apply.py = only writer
│   │   └── live/proxy.py
│   └── tests/ (synthetic fixture tree; never touches G:\)
└── var/                          # cache DB, thumbnails, results (git-ignored)
```

---

## 9. Phasing

| Phase | Deliverable | Done when |
|---|---|---|
| **0. Scaffold** | Both apps run, tokens ported, nav shell, manifest registered and validated | `project_registry.py validate` is clean; empty pages render in the Industry style |
| **1. Indexer + Library (read-only)** | Header indexer with cache, Targets overview, Target page (session matrix, filter totals, flat/dark status), Calibration library page | Totals match a hand count for 3 targets (IC 1848, M 31 mosaic, NGC 7331 without a year folder); a full rescan is incremental |
| **2. Diagnostics v1** | Background jobs running astro-eval metrics + flat checks; session timeline and frame table | Matches astro-eval CLI numbers on the same session |
| **3. Diagnostics v2** | Obstruction, twilight, moon, clouds, dew, focus drift, flat ↔ light mismatch, mispointing | Each detector has a synthetic test and is spot-checked on a real night (e.g. M 31 2026-08-21 mispoints) |
| **4. Planning** | Sites, vendored SNR/ephemeris, imaging-time ranking, moon-aware filter suggestion, calendar, goals | Vendored checks pass; tonight's ranking agrees with AstroPlanner for the same inputs |
| **5. Live + auto-ingest** | AstroController proxy, live pages, `.Download` watcher with dry-run/auto modes, target mapping, held-file alerts, night close, incremental tonight-diagnostics | Works end-to-end against `astrocontroller --fake` writing into a fixture `.Download`; one real night run in dry-run whose proposed moves you check before enabling `Auto` |
| **6. Organizer** | Lint, canonical-layout dry-run plans, approval flow, journal + undo, quarantine action, ingest assistant | Tested on a copied fixture tree first; the first real apply is on one session with you watching |

Phases 1–2 deliver the most value. Planning and Live are independent of each other and can be reordered.

---

## 10. Suggested extras (nice to have, not committed)

- **Disk usage** per target split into raw / calibrated / intermediates / finished, with "safe to archive" *suggestions* for WBPP and `1.`–`7.` intermediates once `.Finished` exists. These are suggestions only; any move is approved like everything else.
- **Year-in-review**: nights out, hours per filter, best FWHM night, targets finished.
- **Rig history**: detect equipment changes from `INSTRUME`/`TELESCOP`/`FOCALLEN` and segment stats, so old-camera data isn't mixed in.
- **Hand-off to AstroMaster**: a "Prepare for processing" button that writes the selected good frames + matching calibration list for AstroMaster/WBPP.
- **AstroBlog export**: per-filter frame counts and integration time, already in AstroBlog's acquisition-panel shape.
- **NINA target sync**: open the planned target as a NINA target JSON in `.NINA\Targets`. This is a new file only and never modifies existing ones.

---

## 11. Open questions — all settled (2026-10-08)

1. ~~**Canonical layout**~~ — **decided** (§3.2): no year level; revisions are separate target folders (` R2` suffix); sessions are `NIGHT_<date>`; mosaics use `<ID>_Panel_<n>` (e.g. `M31_Panel_1`); flats go in `NIGHT_x\FLAT\<F>`.
2. ~~**Session flags**~~ — **decided**: no flag convention. The legacy `[GARBAGE]` / `.OLDCAMERA` sessions are removed during migration (to `.Trash`, after approval). `1.`–`7.` folders are dropped for new targets; non-empty existing ones are kept.
3. ~~**Where does the dashboard run?**~~ — **decided**: on the machine with `G:\` (local disk, so the file watcher is reliable). `.Download\` is auto-ingested (§5.1).
4. ~~**Goals**~~ — **decided** (§6.4): per filter, choose **hours**, **SNR** or **none**. SNR-derived hours that look unrealistic are flagged, with a one-click switch to hours.
5. ~~**Dark-site list**~~ — **decided** (§6.1): sites are edited on **Settings → Sites** and selected in the Planner. No sites need to be provided up front.
