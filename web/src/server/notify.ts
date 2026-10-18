import "server-only";

import { engineGet, engineSend } from "@/lib/engine";
import { isoDate } from "@/lib/planning/night";
import type { QualityLevel } from "@/lib/planning/quality";
import { FILTER_NAMES } from "@/lib/planning/rig";
import type { NotifySettings } from "@/lib/planning/types";
import { loadPlanningData, ratedNights, type RatedNight } from "./planning";

/**
 * Tonight's Planning verdict as a phone notification, through ntfy (ntfy.sh or
 * a self-hosted server: free, no account; subscribe to the topic in the app).
 *
 * The verdict is the Planning page's own (quality.ts), so the phone and the
 * dashboard never disagree. A scheduled task POSTs /api/notify/tonight a few
 * times a day; the engine keeps the last verdict, so a later check only sends
 * when it changed: a night that turned good, changed level, or stopped being
 * worth it ("update" messages).
 */

export type NotifyState = { date: string; sent: boolean; level: QualityLevel; label: string; checkedAt: number };

export type Message = { title: string; message: string; priority: number; tags: string[]; click?: string };

export type CheckResult = {
  date: string;
  site: string;
  level: QualityLevel;
  label: string;
  /** The night is one this setting notifies about. */
  worthIt: boolean;
  /** What was sent (or would be, on a preview); null when nothing is due. */
  message: Message | null;
  sent: boolean;
  reason: string;
};

export const NOTIFY_DEFAULTS: NotifySettings = { enabled: false, ntfyServer: "https://ntfy.sh", ntfyTopic: "", moonlit: true, dashboardUrl: "" };

const clock = (ms: number) => new Date(ms).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const h = (x: number) => `${x.toFixed(1)} h`;

/** 🌑…🌘 from the phase angle (0 new, 90 first quarter, 180 full, 270 last quarter). */
const MOONS = ["🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘"];
const moonEmoji = (phase: number) => MOONS[Math.round((((phase % 360) + 360) % 360) / 45) % 8];

/** The nights this setting notifies about: Excellent / Good, and clear-but-moonlit ones unless turned off. */
export const worthIt = (level: QualityLevel, moonlit: boolean) => level === "excellent" || level === "good" || (moonlit && level === "moonlit");

/**
 * The full night summary: clear window, model agreement, moon, seeing/transparency, dew, best target.
 * Every line starts with an emoji: a collapsed phone notification joins the lines into one, where the
 * emoji still separate them; expanded, they read as a list.
 */
export function nightMessage({ night, quality: q, forecast: f }: RatedNight, siteName: string, link: string | null): Message {
  const lines: string[] = [];
  if (f?.longestRun) lines.push(`🌌 Clear ${clock(f.longestRun.start)} → ${clock(f.longestRun.end)} (${h(f.longestClear)} in a row) · ${siteName}`);
  else lines.push(`🌌 ${q.summary} · ${siteName}`);
  if (q.agreement != null) {
    const runs = [f?.models.gfs ? `GFS ${h(f.models.gfs.longestClear)}` : null, f?.models.icon ? `ICON ${h(f.models.icon.longestClear)}` : null].filter(Boolean).join(", ");
    lines.push(`📊 Models agree ${Math.round(q.agreement * 100)} %${runs ? ` (${runs})` : ""}`);
  }
  const moonShare = night.darkHours ? night.moonFreeHours / night.darkHours : 0;
  const dark = `${h(night.darkHours)} dark`;
  const moon =
    night.moonIllum < 0.05
      ? `New moon · ${dark}`
      : `Moon ${Math.round(night.moonIllum * 100)} %, ${moonShare >= 0.95 ? `down all night · ${dark}` : moonShare <= 0.05 ? `up all night · ${dark}` : `down ${h(night.moonFreeHours)} of ${dark}`}`;
  lines.push(`${moonEmoji(night.moonPhase)} ${moon}`);
  const sky = [
    f?.seeing != null ? `Seeing ${f.seeing.toFixed(1)}/5` : null,
    f?.transparency != null ? `Transparency ${Math.round(f.transparency)}${f.transparency > 20 ? " (hazy)" : ""}` : null,
    f?.meanCloud != null ? `Cloud ${Math.round(f.meanCloud)} %` : null,
  ].filter(Boolean);
  if (sky.length) lines.push(`✨ ${sky.join(" · ")}`);
  if (f?.smoke != null && f.smoke > 0) lines.push("🔥 Wildfire smoke in the forecast");
  if (q.dewFrom != null) lines.push(`💧 Dew risk from ${clock(q.dewFrom)}`);
  if (night.best) lines.push(`🎯 Best target: ${night.best.title} · ${FILTER_NAMES[night.best.letter] ?? night.best.letter} · ${h(night.best.hours)}`);
  return {
    title: `Tonight: ${q.label}${q.weatherKnown ? ` · ${h(q.hours)} clear` : ""}`,
    message: lines.join("\n"),
    priority: q.level === "excellent" ? 4 : 3,
    tags: ["telescope"],
    ...(link ? { click: link } : {}),
  };
}

async function settings(): Promise<NotifySettings> {
  const res = await engineGet<{ settings: { notify?: Partial<NotifySettings> } }>("/api/settings");
  return { ...NOTIFY_DEFAULTS, ...(res.ok ? res.data.settings.notify : {}) };
}

export async function publish(s: NotifySettings, m: Message): Promise<void> {
  // JSON publishing (POST to the server root) keeps emoji and accents intact, unlike ntfy's header form.
  const res = await fetch(s.ntfyServer.replace(/\/+$/, ""), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ topic: s.ntfyTopic, ...m }),
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`ntfy answered ${res.status}: ${(await res.text()).slice(0, 200)}`);
}

/**
 * Rate tonight and decide what to send. `mode`: "check" sends only what is due (and records it),
 * "test" always sends (not recorded), "preview" sends nothing.
 */
export async function checkTonight(mode: "check" | "test" | "preview"): Promise<{ ok: true; result: CheckResult } | { ok: false; status: number; error: string }> {
  const s = await settings();
  if (mode !== "preview" && !s.ntfyTopic) return { ok: false, status: 400, error: "Set an ntfy topic in Settings → Notifications first." };
  if (mode === "check" && !s.enabled) return { ok: false, status: 409, error: "Notifications are off (Settings → Notifications)." };

  const data = await loadPlanningData();
  if (!data.ok) return { ok: false, status: 503, error: data.message };
  const date = isoDate(new Date());
  const rated = await ratedNights(data.data, null, date, 1);
  if (!rated) return { ok: false, status: 404, error: "No site yet (Settings → Sites)." };
  const tonight = rated.nights[0];
  const site = rated.result.site;
  const { level, label } = tonight.quality;
  const good = worthIt(level, s.moonlit);
  const link = s.dashboardUrl ? `${s.dashboardUrl.replace(/\/+$/, "")}/planning/night/${date}` : null;
  const full = nightMessage(tonight, site.name, link);
  const base = { date, site: site.name, level, label, worthIt: good };

  if (mode === "preview") return { ok: true, result: { ...base, message: full, sent: false, reason: good ? "Would notify" : "Tonight is not worth a notification" } };
  if (mode === "test") {
    await publish(s, { ...full, title: `Test · ${full.title}` });
    return { ok: true, result: { ...base, message: full, sent: true, reason: "Test sent" } };
  }

  const state = await engineGet<{ state: NotifyState | null }>("/api/notify/state");
  const prev = state.ok && state.data.state?.date === date ? state.data.state : null;
  let message: Message | null = null;
  let reason: string;
  if (good && !prev?.sent) {
    message = full;
    reason = "Tonight looks good";
  } else if (good && prev && prev.level !== level) {
    message = { ...full, title: `Update · ${full.title}` };
    reason = `Changed from ${prev.label}`;
  } else if (!good && prev?.sent) {
    message = {
      title: `Update · Tonight: ${label}`,
      message: `⚠️ No longer worth setting up: ${tonight.quality.summary} (was ${prev.label}).\n${full.message}`,
      priority: 3,
      tags: ["cloud"],
      ...(link ? { click: link } : {}),
    };
    reason = `No longer good (was ${prev.label})`;
  } else reason = good ? "Already sent" : "Tonight is not worth a notification";

  if (message) await publish(s, message);
  const sent = message ? good : (prev?.sent ?? false);
  await engineSend("/api/notify/state", { date, sent, level, label, checkedAt: Date.now() } satisfies NotifyState, "PUT");
  return { ok: true, result: { ...base, message, sent: message != null, reason } };
}

