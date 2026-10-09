/** AstroController state (as proxied by the engine) and auto-ingest payloads. Loose: AstroController owns the shape. */

export type AcStep = { id: string; name: string; status: string; depth: number; is_container: boolean; parent_id: string | null };

export type AcFrame = {
  index: number;
  date: string;
  filename: string;
  target: string | null;
  image_type: string;
  filter: string | null;
  exposure_s: number | null;
  hfr: number | null;
  stars: number | null;
  temperature: number | null;
  flags?: { saturated?: boolean; cloud_suspect?: boolean; tracking_suspect?: boolean; star_drop_pct?: number | null; hfr_excursion_pct?: number | null; notes?: string[] };
};

export type AcState = {
  nina?: {
    connected?: boolean;
    equipment?: {
      camera?: { Connected?: boolean; Name?: string; Temperature?: number; CoolerOn?: boolean; CoolerPower?: number; TemperatureSetPoint?: number; IsExposing?: boolean };
      mount?: { Connected?: boolean; Name?: string; Altitude?: number; Azimuth?: number; SideOfPier?: string; TimeToMeridianFlip?: number; TrackingEnabled?: boolean; AtPark?: boolean };
      focuser?: { Connected?: boolean; Position?: number; Temperature?: number };
      filterwheel?: { Connected?: boolean; SelectedFilter?: { Name?: string } };
      guider?: { State?: string };
    };
  };
  phd2?: { connected?: boolean; app_state?: string; settling?: boolean; pixel_scale?: number; guiding_for_s?: number; last_alert?: unknown };
  sequence?: { available?: boolean; error?: string | null; steps?: AcStep[] };
  guiding?: {
    rms?: { rms_ra?: number; rms_dec?: number; rms_total?: number; peak_ra?: number; peak_dec?: number; n?: number; snr_med?: number };
    graph?: { t: number[]; ra: number[]; dec: number[] };
  };
  frames?: AcFrame[];
  preview?: { available?: boolean; filename?: string; token?: string; source?: string };
  guide_image?: { token?: string };
  weather?: {
    error?: string | null;
    current?: { cloud_total?: number; humidity?: number; temp_c?: number; dewpoint_c?: number; dewpoint_spread?: number; wind_ms?: number; gust_ms?: number; precip_prob?: number };
    summary?: { clear_hours?: number; dew_risk?: boolean; rain_risk?: boolean; max_gust_ms?: number };
  };
  sky?: { illumination?: number; phase_name?: string; altitude_deg?: number; separation_deg?: number | null; up?: boolean };
  advisor?: { mode?: string; enabled?: boolean; last?: { summary?: string; decision?: string } | null };
  server_time?: number;
};

export type LivePayload = { online: boolean; url: string; state?: AcState; error?: string; lastOk?: number | null; tokenSet: boolean };

export type IngestAction = {
  op: "move" | "copy";
  src: string;
  dst: string;
  night: string;
  download_night: string;
  kind: string;
  filter: string | null;
  revision_id: string;
  target: string;
  size: number;
};

export type IngestHold = {
  src: string;
  download_night: string;
  reason: string;
  message: string;
  kind: string | null;
  object: string | null;
  filter: string | null;
  prompt: boolean;
};

export type IngestNightPlan = { lights: number; flats: number; closed: boolean; newestLight: string | null; emptyDirs?: number };

export type IngestJournalNight = { moved: number; copied: number; undone: number; errors: number; targets: Record<string, number>; last: number; emptyDirs: number };

export type IngestStatus = {
  mode: "off" | "dry-run" | "auto";
  lastRun: { at: number; mode: string; moved: number; copied: number; errors: number } | null;
  error: string | null;
  downloadRoot: string;
  downloadExists: boolean;
  plan: { actions: IngestAction[]; holds: IngestHold[]; skipped: IngestHold[]; nights: Record<string, IngestNightPlan>; scannedAt: number } | null;
  closed: string[];
  /** Nights you undid: Auto leaves them alone until resumed. */
  paused: string[];
  journal: Record<string, IngestJournalNight>;
  recent: { id: number; ts: number; downloadNight: string; op: string; src: string; dst: string; revision: string; kind: string; filter: string | null; undone: number | null; error: string | null }[];
  objectMap: { object: string; catalogId: string | null; revisionId: string | null; learned: boolean; frames?: number }[];
  targets: { id: string; folder: string; catalogId: string; stage: string; revision: number }[];
};

export const fileName = (p: string) => p.split(/[\\/]/).pop() ?? p;

const FILTER_NAME_TO_LETTER: Record<string, string> = {
  ha: "H", h: "H", halpha: "H", "h-alpha": "H", oiii: "O", o3: "O", o: "O", sii: "S", s2: "S", s: "S",
  l: "L", lum: "L", luminance: "L", clear: "L", r: "R", red: "R", g: "G", green: "G", b: "B", blue: "B",
};

/** NINA filter name ("Ha", "OIII", "Lum") → the dashboard's letter, so colours match the Library. */
export const filterLetterOf = (name: string | null | undefined) => (name ? FILTER_NAME_TO_LETTER[name.trim().toLowerCase()] ?? name : null);
