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
  nights?: number;
  lastNight?: string | null;
};

export type GoalMode = "hours" | "snr" | "none";
export type Goal = { mode: GoalMode; hours?: number; snr?: number };
export type Goals = Record<string, Goal>;

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
  /** Live: stop the NINA sequence while the guide star is lost, resume it once guiding is steady. */
  guard?: GuardSettings;
  /** Planning: push tonight's verdict to a phone through ntfy (see server/notify.ts). */
  notify?: NotifySettings;
};

export type NotifySettings = { enabled: boolean; ntfyServer: string; ntfyTopic: string; moonlit: boolean; dashboardUrl: string };

export type ObservatorySettings = { ninaHost: string; ninaPort: number; phd2Host: string; phd2Instance: number };
export type GuardSettings = { enabled: boolean; stopAfterS: number; resumeAfterS: number; maxWaitMin: number };
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
};

export type SkyPayload = { sessions: number; samples: SkySample[]; pedestals: Record<string, { adu: number; frames: number } | null> };

export type HalphaMeasurement = {
  survey: string;
  objectR: number;
  backgroundR: number;
  netR: number;
  peakR: number | null;
  objectArcmin: number;
  beamDiluted: boolean;
  cached?: boolean;
};
