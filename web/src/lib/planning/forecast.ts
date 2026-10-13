/**
 * Astrospheric's hourly forecast (fetched and cached by the engine), cut to a
 * night's astronomical darkness. Each value keeps Astrospheric's own colour,
 * which encodes its scale (seeing and transparency have no documented units).
 */

export type ForecastValue = { value: number; color: string | null };

export type ForecastHour = {
  t: string;
  /** Hours this value covers, centred on `t`: 1, or 3 for the extended (GDPS) cloud forecast. */
  step?: number;
  /** From Astrospheric's long-range extended cloud forecast: cloud only, less reliable. */
  extended?: boolean;
  cloud?: ForecastValue;
  transparency?: ForecastValue;
  seeing?: ForecastValue;
  temperature?: ForecastValue;
  dewPoint?: ForecastValue;
  wind?: ForecastValue;
  windDir?: ForecastValue;
  /** Second and third cloud opinions (GFS, ICON) over the hourly range, for model agreement. */
  gfsCloud?: ForecastValue;
  iconCloud?: ForecastValue;
  /** Column smoke (RAP), ~2 days ahead; unit undocumented, 0 = none. */
  smoke?: ForecastValue;
};

export type Forecast = {
  modelTime: string | null;
  timeZone: string | null;
  hours: ForecastHour[];
  credits: { cost: number | null; remaining: number | null };
};

export type ForecastPayload = {
  keySet: boolean;
  siteId: string;
  cached?: boolean;
  stale?: boolean;
  fetchedAt?: number | null;
  forecast: Forecast | null;
  error: string | null;
};

/** One model's view of the night's clear sky. */
export type ModelClear = { clearHours: number; longestClear: number };

export type NightForecast = {
  hours: (ForecastHour & { ms: number })[];
  /** Share of the darkness the forecast reaches (~3½ days hourly, then 3-hourly cloud to ~10 days). */
  covered: number;
  meanCloud: number | null;
  /** Hours of darkness with cloud below CLEAR_CLOUD. */
  clearHours: number;
  /** The longest unbroken run of clear dark hours. */
  longestClear: number;
  /** When that run starts and ends (ms, clipped to the darkness); null when nothing is clear. */
  longestRun: { start: number; end: number } | null;
  /** Mean transparency over the clear dark hours (Astrospheric scale, lower is better). */
  transparency: number | null;
  /** Mean seeing (1–5) over the clear dark hours, or over the whole night when nothing is clear. */
  seeing: number | null;
  /** The clear run still going when the forecast stops (0 if the last forecast hour is cloudy). */
  clearAtEnd: number;
  /** Dark hours after the forecast stops. */
  uncoveredHours: number;
  /** Some of the night rests on the extended (3-hourly, cloud-only) forecast. */
  extended: boolean;
  /** Temperature within DEW_MARGIN of the dew point at some hour. */
  dewRisk: boolean;
  /** When that first happens in the darkness (ms), null without dew risk. */
  dewFrom: number | null;
  /**
   * 0–1: how often GFS and ICON make the same clear/cloudy call as Astrospheric, over the dark
   * hours where at least one of them has a value. Null when they do not reach this night.
   */
  agreement: number | null;
  models: { gfs: ModelClear | null; icon: ModelClear | null };
  /** Highest column smoke over the dark hours (null when not forecast). */
  smoke: number | null;
};

export const CLEAR_CLOUD = 30;
const DEW_MARGIN = 2;
const HOUR = 3_600_000;
/** Agreement needs the other models on at least this share of the dark hours Astrospheric covers. */
const MIN_COMPARED = 0.5;

type Slot = { h: ForecastHour; ms: number; start: number; end: number; overlap: number };

/** Clear hours and the longest unbroken clear run for one cloud series; a cloudy slot or a gap ends a run. */
function clearRuns(slots: Slot[], cloud: (h: ForecastHour) => number | undefined) {
  let clear = 0;
  let run = 0;
  let longest = 0;
  let lastEnd = -Infinity;
  let n = 0;
  let runStart = 0;
  let span: { start: number; end: number } | null = null;
  for (const s of slots) {
    const c = cloud(s.h);
    if (c == null) {
      run = 0;
      continue;
    }
    n++;
    if (c < CLEAR_CLOUD) {
      clear += s.overlap;
      if (!(s.start <= lastEnd + 1 && run > 0)) {
        run = 0;
        runStart = s.start;
      }
      run += s.overlap;
      if (run > longest) span = { start: runStart, end: s.end };
    } else run = 0;
    longest = Math.max(longest, run);
    lastEnd = s.end;
  }
  return { clearHours: clear, longestClear: longest, runAtEnd: run, slots: n, span };
}

/** Each value stands for the `step` hours centred on it; only the part inside [dusk, dawn] counts. */
export function nightForecast(f: Forecast | null, dusk: number | null, dawn: number | null): NightForecast | null {
  if (!f || dusk == null || dawn == null || dawn <= dusk) return null;
  const slots: Slot[] = [];
  for (const h of [...f.hours].sort((a, b) => Date.parse(a.t) - Date.parse(b.t))) {
    const ms = Date.parse(h.t);
    const half = ((h.step ?? 1) * HOUR) / 2;
    const overlap = Math.max(0, Math.min(ms + half, dawn) - Math.max(ms - half, dusk)) / HOUR;
    if (overlap > 0) slots.push({ h, ms, start: ms - half, end: ms + half, overlap });
  }
  if (!slots.length) return null;

  const main = clearRuns(slots, (h) => h.cloud?.value);
  const withCloud = slots.filter((s) => s.h.cloud);
  const covered = withCloud.reduce((a, s) => a + s.overlap, 0);
  const cloudSum = withCloud.reduce((a, s) => a + s.h.cloud!.value * s.overlap, 0);
  const coverEnd = withCloud.length ? Math.min(withCloud[withCloud.length - 1].end, dawn) : dusk;

  // Model agreement: per dark hour, the share of GFS/ICON making Astrospheric's clear/cloudy call.
  let agreeSum = 0;
  let compared = 0;
  for (const s of withCloud) {
    const others = [s.h.gfsCloud?.value, s.h.iconCloud?.value].filter((v): v is number => v != null);
    if (!others.length) continue;
    const clear = s.h.cloud!.value < CLEAR_CLOUD;
    agreeSum += (others.filter((v) => v < CLEAR_CLOUD === clear).length / others.length) * s.overlap;
    compared += s.overlap;
  }
  const model = (key: "gfsCloud" | "iconCloud"): ModelClear | null => {
    const r = clearRuns(slots, (h) => h[key]?.value);
    return r.slots ? { clearHours: r.clearHours, longestClear: r.longestClear } : null;
  };
  const smokes = slots.map((s) => s.h.smoke?.value).filter((v): v is number => v != null);
  const clearSlots = withCloud.filter((s) => s.h.cloud!.value < CLEAR_CLOUD);
  const dewSlot = slots.find((s) => s.h.temperature && s.h.dewPoint && s.h.temperature.value - s.h.dewPoint.value < DEW_MARGIN);

  return {
    hours: slots.map((s) => ({ ...s.h, ms: s.ms })),
    covered: Math.min(1, covered / ((dawn - dusk) / HOUR)),
    meanCloud: covered ? cloudSum / covered : null,
    clearHours: main.clearHours,
    longestClear: main.longestClear,
    longestRun: main.span ? { start: Math.max(main.span.start, dusk), end: Math.min(main.span.end, dawn) } : null,
    transparency: mean(clearSlots.map((s) => s.h.transparency?.value)),
    seeing: mean(clearSlots.map((s) => s.h.seeing?.value)) ?? mean(slots.map((s) => s.h.seeing?.value)),
    clearAtEnd: main.runAtEnd,
    uncoveredHours: Math.max(0, (dawn - coverEnd) / HOUR),
    extended: slots.some((s) => s.h.extended),
    dewRisk: dewSlot != null,
    dewFrom: dewSlot ? Math.max(dewSlot.start, dusk) : null,
    agreement: covered && compared / covered >= MIN_COMPARED ? agreeSum / compared : null,
    models: { gfs: model("gfsCloud"), icon: model("iconCloud") },
    smoke: smokes.length ? Math.max(...smokes) : null,
  };
}

function mean(xs: (number | undefined)[]): number | null {
  const v = xs.filter((x): x is number => x != null);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
}
