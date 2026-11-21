/** Engine payloads for Planning (see engine/astrodash/api_planning.py). */

export type HorizonPoint = { az: number; alt: number };

export type Site = {
  id: string;
  name: string;
  lat: number;
  lon: number;
  elevation: number | null;
  /** Zenith sky brightness with no moon, mag/arcsec² (SQM). */
  sqm: number;
  minAlt: number;
  /** Optional per-azimuth obstruction profile, linearly interpolated (wraps at 360°). */
  horizon: HorizonPoint[];
  isDefault: boolean;
  notes: string;
  /** YYYY-MM-DD: frames before it don't calibrate this site's sky (the scope moved, the lighting changed). */
  calibrateSince?: string | null;
  nights?: number;
  lastNight?: string | null;
};

export type GoalMode = "hours" | "match" | "none";
/** hours: as set; match: as good as the revision `ref` in this filter (see goals.ts). */
export type Goal = { mode: GoalMode; hours?: number; ref?: string };
export type Goals = Record<string, Goal>;

/** Engine `api_match.py`: the quality a target gathers per hour in one filter, measured from its subs. */
export type MatchFilter = { qPerHour: number; spread: number | null; frames: number; nights: number };
export type MatchRevision = {
  revisionId: string;
  filters: Record<string, MatchFilter>;
  maskFilter: Record<string, string>;
  scale: number | null;
  skipped: { noFlat: number; noPedestal: number; unreadable: number };
  analysedNights: number;
  unanalysedNights: number;
  pending: boolean;
  job: { label: string; status: string; done: number; total: number; error: string | null } | null;
};

export type PlanFilterTotals = {
  filter: string;
  seconds: number;
  /** Seconds kept by diagnostics where a night was analysed; raw seconds elsewhere. */
  goodSeconds: number;
  analysedSeconds: number;
};

export type PlanTarget = {
  id: string;
  folder: string;
  catalogId: string;
  name: string;
  revision: number;
  groupId: string;
  stage: string;
  pointing: { ra: number; dec: number } | null;
  panels: string[];
  lastNight: string | null;
  nights: number;
  filters: PlanFilterTotals[];
  goals: Goals;
  goalsSource: "dashboard" | "target.json" | null;
};

export type DetectedRig = {
  instrume: string | null;
  telescop: string | null;
  focalLength: number | null;
  pixelSizeUm: number | null;
  widthPx: number | null;
  heightPx: number | null;
  frames: number;
  filters: string[];
  subSeconds: Record<string, number>;
};

/** What the user saved on Settings → Rig. Every field optional; the rest comes from presets/headers. */
export type RigSettings = {
  telescopeId?: string;
  apertureMm?: number;
  focalLengthMm?: number;
  cameraId?: string;
  readNoiseE?: number;
  /** Filter letter → AstroPlanner filter preset id (e.g. "H" → "ha-3"). */
  filterPresets?: Record<string, string>;
  subSeconds?: Record<string, number>;
};

export type DashSettings = {
  goalCapHours: number;
  rig: RigSettings | null;
  /** Live: where NINA (Advanced API) and PHD2 (event server) run. */
  observatory?: ObservatorySettings;
  /** Live → Guiding: the AI advisor. Its API key is read from the engine's .env, never stored here. */
  advisor?: AdvisorSettings;
  /** Live: the sky monitor NINA reads as its safety monitor (engine `sky.py`, served by `alpaca.py`). */
  skyMonitor?: SkyMonitorSettings;
  /** Planning: push tonight's verdict to a phone through ntfy (see server/notify.ts). */
  notify?: NotifySettings;
  /** Live · Ingest: the folder NINA's frames land in (the SMB share's local path); "" = <data root>\.Download. */
  ingest?: IngestSettings;
  /** Library: more folders holding target folders (finished targets moved to another disk); read only. */
  library?: LibraryPathSettings;
};

export type NotifySettings = { enabled: boolean; ntfyServer: string; ntfyTopic: string; moonlit: boolean; dashboardUrl: string };

export type IngestSettings = { downloadDir: string };
export type LibraryPathSettings = { archiveDirs: string[] };
export type ObservatorySettings = { ninaHost: string; ninaPort: number; phd2Host: string; phd2Instance: number };
export type SkyMonitorSettings = {
  enabled: boolean;
  /** Unsafe when the guide star was lost `lostPct` of the last `windowS`, or its brightness there is under `dimPct` of clear sky. */
  windowS: number;
  lostPct: number;
  dimPct: number;
  /** Clear again once PHD2 has guided this long without losing the star, bright again. */
  clearAfterS: number;
  /** Unsafe this long without a break: clear until the next guiding run (an obstruction is more likely). */
  giveUpMin: number;
};
export type ReasoningEffort = "none" | "low" | "medium" | "high";
export type AdvisorSettings = { enabled: boolean; model: string; ollamaUrl: string; intervalMin: number; targetRms: number; effort: ReasoningEffort; autoApply: boolean };

export type PlanningContext = {
  targets: PlanTarget[];
  sites: Site[];
  settings: DashSettings;
  detectedRig: DetectedRig | null;
};

export type SkySample = {
  revisionId: string;
  session: string;
  filter: string;
  time: string | null;
  exposure: number;
  ra: number | null;
  dec: number | null;
  lat: number;
  lon: number;
  altitude: number | null;
  skyE: number;
  skyRate: number;
  gainEPerAdu: number;
  /** From the frame's header (XPIXSZ includes binning; FOCALLEN), when present. */
  pixelSizeUm?: number | null;
  focalLength?: number | null;
};

export type SkyPayload = { sessions: number; samples: SkySample[]; pedestals: Record<string, { adu: number; frames: number } | null> };
