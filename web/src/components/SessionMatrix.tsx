import Link from "next/link";
import { Activity, Moon } from "lucide-react";

import { exposure, FILTER_ORDER, filterLabel, hours, num, settingsLabel, shortDate } from "@/lib/format";
import type { DarkStatus, FlatStatus, LightGroup, Level, Revision, Session } from "@/lib/types";
import { Badge, FilterChip, StatusIcon, Tip } from "./ui";
import styles from "./SessionMatrix.module.css";

const FLAT_LEVEL: Record<FlatStatus["status"], Level> = {
  ok: "ok",
  master: "ok",
  borrowed: "warn",
  missing: "bad",
  "binning-mismatch": "bad",
};

function flatLevel(f: FlatStatus | undefined): Level {
  if (!f) return "bad";
  if (f.status === "ok") {
    if (f.readoutWrong?.length) return "bad";
    if (f.focusWarning || f.calibration?.some((c) => c.status !== "ok")) return "warn";
  }
  return FLAT_LEVEL[f.status];
}

function flatText(f: FlatStatus | undefined): string {
  if (!f) return "No flats";
  switch (f.status) {
    case "ok":
      return [
        `${f.frames} flats${f.exposures?.length ? ` · ${f.exposures.map(exposure).join(", ")}` : ""}`,
        f.readoutWrong?.length ? `shot in ${f.readoutWrong.join(", ")} — flats must be Full Resolution` : null,
        f.focusWarning ? `focuser moved ${f.focusDelta} steps since the lights` : null,
        f.readoutAccepted?.length ? `shot in ${f.readoutAccepted.join(", ")} (finished target, not flagged)` : null,
        ...(f.calibration ?? []).map((c) =>
          c.status === "ok"
            ? `calibrated by ${c.source}`
            : c.status === "suspect"
              ? `only a suspect bias: ${c.source} (${c.reason})`
              : `no bias/dark-flats at ${c.settings}`,
        ),
      ]
        .filter(Boolean)
        .join(" · ");
    case "master":
      return `Master flat only (${f.file})`;
    case "borrowed":
      return `No flats this night — nearest flats ${shortDate(f.from)}`;
    case "binning-mismatch":
      return `${f.frames} flats, but binned differently from the lights`;
    default:
      return "No flats for this filter";
  }
}

const DARK_LEVEL: Record<DarkStatus, Level> = {
  ok: "ok",
  unverified: "info",
  "raw-only": "warn",
  missing: "bad",
  dismissed: "info",
};
const DARK_TEXT: Record<DarkStatus, string> = {
  ok: "Matching master dark",
  unverified: "Master dark found; gain/offset not verifiable",
  "raw-only": "Raw darks only — no master integrated",
  missing: "No matching master dark",
  dismissed: "No matching master dark — dismissed",
};

function worstDark(groups: LightGroup[]): DarkStatus | null {
  const order: DarkStatus[] = ["missing", "raw-only", "dismissed", "unverified", "ok"];
  const present = groups.map((g) => g.dark?.status).filter(Boolean) as DarkStatus[];
  return order.find((s) => present.includes(s)) ?? null;
}

function Cell({ session, filter, panels }: { session: Session; filter: string; panels: string[] }) {
  const groups = session.lightGroups.filter((g) => (g.filter ?? "?") === filter);
  const usable = groups.filter((g) => g.frames > 0);
  const setAside = groups.reduce((s, g) => s + g.rejected + g.quarantined, 0);
  if (!usable.length) {
    return setAside ? (
      <td className={styles.cell}>
        <span className="muted num">{setAside} set aside</span>
      </td>
    ) : (
      <td className={`${styles.cell} ${styles.empty}`}>
        <span aria-label="No lights">—</span>
      </td>
    );
  }
  const seconds = usable.reduce((s, g) => s + g.seconds, 0);
  const exposures = [...new Set(usable.map((g) => g.exposure))];
  const byExposure = exposures.map((e) => ({
    e,
    frames: usable.filter((g) => g.exposure === e).reduce((s, g) => s + g.frames, 0),
  }));
  const flat = session.flatStatus[filter];
  const flatLvl = flatLevel(flat);
  const dark = worstDark(usable);
  const darkGroup = usable.find((g) => g.dark?.status === dark);

  return (
    <td className={styles.cell}>
      <div className={`num ${styles.frames}`}>
        {byExposure.map((x) => (
          <span key={String(x.e)}>
            {x.frames}×{exposure(x.e).replace(" s", "s")}
          </span>
        ))}
      </div>
      <div className={`num ${styles.hours}`}>{hours(seconds)}</div>
      {panels.length ? (
        <div className={`num ${styles.panels}`}>
          {panels.map((p) => {
            const n = usable.filter((g) => g.panel === p).reduce((s, g) => s + g.frames, 0);
            return (
              <span key={p} className={n ? undefined : styles.panelMissing}>
                P{p} {n}
              </span>
            );
          })}
        </div>
      ) : null}
      <div className={styles.marks}>
        <Tip tip={<><strong>Flats</strong><br />{flatText(flat)}</>}>
          <span className={styles.mark}>
            <StatusIcon level={flatLvl} size={13} label={`Flats: ${flatText(flat)}`} />F
          </span>
        </Tip>
        {dark ? (
          <Tip
            tip={
              <>
                <strong>Darks</strong>
                <br />
                {DARK_TEXT[dark]}
                {darkGroup?.dark?.note ? (
                  <>
                    <br />
                    <em>{darkGroup.dark.note}</em>
                  </>
                ) : null}
                {darkGroup?.dark?.master ? (
                  <>
                    <br />
                    <span className="mono">
                      {darkGroup.dark.master.file} · {darkGroup.dark.master.build}
                    </span>
                    {darkGroup.gainIgnored ? (
                      <>
                        <br />
                        {darkGroup.readout}: gain/offset not used by the camera
                      </>
                    ) : null}
                  </>
                ) : (
                  <>
                    <br />
                    <span className="mono">
                      {exposure(darkGroup?.exposure)} · {darkGroup ? settingsLabel(darkGroup) : "?"} ·{" "}
                      {darkGroup?.setTemp ?? "?"} °C
                    </span>
                  </>
                )}
              </>
            }
          >
            <span className={styles.mark}>
              <StatusIcon level={DARK_LEVEL[dark]} size={13} label={`Darks: ${DARK_TEXT[dark]}`} />D
            </span>
          </Tip>
        ) : null}
        {setAside ? <span className={`num ${styles.setAside}`}>−{setAside}</span> : null}
      </div>
    </td>
  );
}

function ConditionsCell({ s }: { s: Session }) {
  const c = s.conditions;
  if (!c) return <td className="muted">—</td>;
  return (
    <td className={styles.conditions}>
      <Tip
        tip={
          <span className="num">
            Ambient {num(c.ambientC, 1)} °C · dew point {num(c.dewPointC, 1)} °C
            <br />
            Min dew margin {num(c.minDewMarginC, 1)} °C · humidity {num(c.humidity)} %
            <br />
            Wind {num(c.windKph, 1)} km/h · airmass {num(c.airmassMin, 2)}–{num(c.airmassMax, 2)}
            {c.moon ? (
              <>
                <br />
                Moon {Math.round(c.moon.illumination * 100)} % lit · {num(c.moon.separation)}° away · alt{" "}
                {num(c.moon.altitude)}° (mid-session)
              </>
            ) : null}
          </span>
        }
      >
        <span className={`num ${styles.condLine}`}>
          {c.moon ? (
            <span className={styles.moon}>
              <Moon size={12} strokeWidth={1.8} aria-hidden="true" />
              {Math.round(c.moon.illumination * 100)}%
              {c.moon.separation != null ? ` · ${c.moon.separation}°` : ""}
            </span>
          ) : null}
          {c.humidity != null || c.minDewMarginC != null ? (
            <span>
              {[c.humidity != null ? `RH ${num(c.humidity)}%` : null, c.minDewMarginC != null ? `Δdew ${num(c.minDewMarginC, 1)}°` : null]
                .filter(Boolean)
                .join(" · ")}
            </span>
          ) : null}
        </span>
      </Tip>
    </td>
  );
}

export function SessionMatrix({ rev }: { rev: Revision }) {
  const filters = FILTER_ORDER.filter((f) => rev.filters.some((x) => x.filter === f)).concat(
    rev.filters.map((f) => f.filter).filter((f) => !FILTER_ORDER.includes(f)),
  );
  if (!rev.sessions.length) return <p className="muted">No sessions yet.</p>;
  return (
    <div className={styles.scroller}>
      <table className={`table ${styles.matrix}`}>
        <thead>
          <tr>
            <th>Night</th>
            {filters.map((f) => (
              <th key={f} title={filterLabel(f)}>
                <FilterChip filter={f} long />
              </th>
            ))}
            <th style={{ textAlign: "right" }}>Total</th>
            <th>Conditions</th>
          </tr>
        </thead>
        <tbody>
          {rev.sessions.map((s) => (
            <tr key={s.id}>
              <td className={styles.night}>
                <div className={styles.date}>{shortDate(s.date)}</div>
                <div className={styles.folder}>
                  <span className="mono">{s.folder}</span>
                  {s.naming === "legacy" ? <Badge tone="muted">legacy name</Badge> : null}
                </div>
                <Link href={`/diagnostics/${rev.id}/${encodeURIComponent(s.folder)}`} className={styles.diagLink}>
                  <Activity size={11} strokeWidth={1.8} aria-hidden="true" /> Diagnostics
                </Link>
                {s.nightMismatch ? (
                  <div className={styles.note}>
                    <StatusIcon level="info" size={12} /> headers say {s.headerNights.join(", ")}
                  </div>
                ) : null}
              </td>
              {filters.map((f) => (
                <Cell key={f} session={s} filter={f} panels={rev.panels} />
              ))}
              <td className={`num ${styles.total}`}>
                {hours(s.totals.seconds)}
                <div className="muted">{s.totals.lights} fr</div>
              </td>
              <ConditionsCell s={s} />
            </tr>
          ))}
        </tbody>
      </table>
      <p className={styles.key}>
        <StatusIcon level="ok" size={12} /> F flats / D master dark present · <StatusIcon level="warn" size={12} /> needs a look (focus moved, flats not calibratable, borrowed) · <StatusIcon level="bad" size={12} /> missing · −n frames rejected or quarantined. Hover a cell for details.
      </p>
    </div>
  );
}
