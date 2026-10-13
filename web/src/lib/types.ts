// Shapes returned by the engine (engine/astrodash/library.py).

export type Level = "ok" | "info" | "warn" | "bad";

export interface Stage {
  value: string;
  declared: boolean;
  inferred: string;
}

export interface FilterTotal {
  filter: string;
  frames: number;
  seconds: number;
}

export interface RevisionSummary {
  id: string;
  folder: string;
  revision: number;
  stage: Stage;
  hasPreview: boolean;
  panels: string[];
  totals: RevisionTotals;
  filters: FilterTotal[];
  issues: { bad: number; warn: number };
  needsFlats: boolean;
}

export interface RevisionTotals {
  seconds: number;
  lights: number;
  rejected: number;
  quarantined: number;
  nights: number;
  first: string | null;
  last: string | null;
  /** Revision detail only: seconds left after diagnostics rejection (unanalysed nights count in full). */
  acceptedSeconds?: number;
  /** Revision detail only: acquired seconds on nights diagnostics has analysed. */
  analysedSeconds?: number;
}

export interface TargetGroup {
  id: string;
  catalogId: string;
  name: string;
  revisions: RevisionSummary[];
}

export interface Overview {
  scannedAt: string;
  scanDurationS: number;
  frames: number;
  unreadable: number;
  targets: TargetGroup[];
}

export interface MasterInfo {
  kind: "dark" | "bias";
  build: string;
  ageDays: number | null;
  file: string;
  temp: number | null;
  exposure: number | null;
  instrume: string | null;
  gain: number | null;
  offset: number | null;
  readout: string | null;
  settingsKnown: boolean;
  suspect: string | null;
  sources: number;
  exposureFrom: string;
}

export type DarkStatus = "ok" | "unverified" | "raw-only" | "missing" | "dismissed";

export interface LightGroup {
  panel: string | null;
  filter: string | null;
  exposure: number | null;
  gain: number | null;
  offset: number | null;
  readout: string | null;
  binning: number | null;
  setTemp: number | null;
  instrume: string | null;
  gainIgnored: boolean;
  frames: number;
  seconds: number;
  rejected: number;
  quarantined: number;
  focpos: number | null;
  dark: { status: DarkStatus; master: MasterInfo | null; was?: DarkStatus | null; note?: string | null } | null;
  darkKey: string;
}

export interface FlatGroup {
  filter: string | null;
  binning: number | null;
  exposure: number | null;
  frames: number;
  rejected: number;
}

export type FlatStatusValue = "ok" | "master" | "borrowed" | "missing" | "binning-mismatch";

export interface FlatStatus {
  status: FlatStatusValue;
  frames?: number;
  exposures?: number[];
  focusDelta?: number | null;
  focusWarning?: boolean;
  readouts?: string[];
  readoutWrong?: string[];
  readoutAccepted?: string[];
  calibration?: { status: "ok" | "suspect" | "missing"; settings: string; source: string | null; reason?: string | null }[];
  file?: string;
  from?: string;
}

export interface Conditions {
  ambientC: number | null;
  dewPointC: number | null;
  minDewMarginC: number | null;
  humidity: number | null;
  windKph: number | null;
  airmassMin: number | null;
  airmassMax: number | null;
  moon: { illumination: number; separation: number | null; altitude: number | null } | null;
}

export interface Session {
  id: string;
  folder: string;
  date: string | null;
  naming: "canonical" | "legacy";
  headerNights: string[];
  nightMismatch: boolean;
  lightGroups: LightGroup[];
  flatGroups: FlatGroup[];
  flatStatus: Record<string, FlatStatus>;
  start: string | null;
  end: string | null;
  conditions: Conditions | null;
  totals: { lights: number; seconds: number; rejected: number; quarantined: number; flats: number };
  /** Revision detail only: the stored diagnostics run for this night, if any. */
  diagnostic?: { finished: number; acceptedSeconds: number; rejected: number } | null;
}

export interface Check {
  level: Level;
  code: string;
  text: string;
  detail: string[];
}

export interface FilterDetail extends FilterTotal {
  rejected: number;
  quarantined: number;
  sessions: number;
  sessionsWithFlats: number;
  exposures: number[];
  /** Seconds left after diagnostics rejection (unanalysed nights count in full). */
  acceptedSeconds?: number;
  /** Acquired seconds on nights diagnostics has analysed. */
  analysedSeconds?: number;
}

export interface Revision {
  id: string;
  folder: string;
  catalogId: string;
  name: string;
  revision: number;
  groupId: string;
  stage: Stage;
  legacyYears: string[];
  legacyStages: string[];
  wbppMasters: string[];
  finishedFiles: number;
  hasPreview: boolean;
  panels: string[];
  equipment: { instrume: string; telescop: string; focalLength: number }[];
  totals: RevisionTotals;
  filters: FilterDetail[];
  sessions: Session[];
  excludedSessions: { folder: string; reason: string; frames: number; lights: number }[];
  checks: Check[];
  siblings: { id: string; revision: number; folder: string }[];
  unreadable: number;
}

export interface CalibrationLibrary {
  builds: {
    build: string;
    ageDays: number | null;
    masters: MasterInfo[];
    raw: {
      kind: string;
      folder: string;
      exposure: number | null;
      temp: number | null;
      gain: number | null;
      offset: number | null;
      readout: string | null;
      binning: number | null;
      instrume: string | null;
      frames: number;
      suspect: string | null;
    }[];
  }[];
  minBiasExposure: number;
  suspectMasters: MasterInfo[];
  unmatched: {
    key: string;
    was: DarkStatus | null;
    note: string | null;
    exposure: number | null;
    gain: number | null;
    offset: number | null;
    readout: string | null;
    setTemp: number | null;
    instrume: string | null;
    status: DarkStatus;
    uses: { revisionId: string; target: string; session: string; filter: string | null; frames: number }[];
  }[];
  unreadable: number;
}

export interface EngineStatus {
  scan: {
    running: boolean;
    phase: string;
    files_found: number;
    to_read: number;
    read: number;
    started_at: number | null;
    finished_at: number | null;
    error: string | null;
  };
  error: string | null;
  ready: boolean;
  scannedAt: string | null;
  dataRoot: string;
}
