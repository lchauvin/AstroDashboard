"use client";

import { useEffect, useState } from "react";
import { FolderSearch, Plus } from "lucide-react";

import { Marks, StatusIcon } from "../ui";
import type { DarkGroup, DarkSet, DarkSetting, LeakTest } from "@/lib/leaks";
import { settingText } from "@/lib/leaks";
import styles from "./leaks.module.css";

const KEYS: (keyof DarkSetting)[] = ["exposure", "setTemp", "readout", "gain", "offset", "binning", "filter"];
const same = (a: DarkSetting, b: DarkSetting) =>
  KEYS.filter((k) => k !== "filter").every((k) => (a[k] ?? null) === (b[k] ?? null));
const pick = (g: DarkGroup) => ({ folder: g.folder, ...Object.fromEntries(KEYS.map((k) => [k, g[k] ?? null])) });

function parent(folder: string): string {
  const i = Math.max(folder.lastIndexOf("\\"), folder.lastIndexOf("/"));
  return i > 0 ? folder.slice(0, i) : folder;
}

function when(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/**
 * Pick frames for a phase 2 dark set (`kind="set"`) or a phase 1 spot check (`kind="check"`):
 * browse a folder for groups (folder × type × filter × setting), or take a calibration-library group.
 */
export function AddSet({ test, kind = "set", onAdded }: { test: LeakTest; kind?: "set" | "check"; onAdded: (t: LeakTest) => void }) {
  const check = kind === "check";
  const previous: DarkSet | undefined = test.sets[test.sets.length - 1];
  const lastFolder = check ? test.checks[test.checks.length - 1]?.folder ?? previous?.folder : previous?.folder ?? test.checks[test.checks.length - 1]?.folder;
  const [folder, setFolder] = useState(lastFolder ? parent(lastFolder) : "");
  const [groups, setGroups] = useState<DarkGroup[] | null>(null);
  const [picked, setPicked] = useState<number | null>(null);
  const [reference, setReference] = useState<number | null>(null);
  const [library, setLibrary] = useState<DarkGroup[]>([]);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (check) return;
    fetch("/engine/api/leaks/library-groups", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : { groups: [] }))
      .then((b: { groups: DarkGroup[] }) => setLibrary(b.groups))
      .catch(() => setLibrary([]));
  }, [check]);

  function choose(rows: DarkGroup[]) {
    setGroups(rows);
    setReference(null);
    const used = new Set([...test.sets, ...test.checks].map((s) => `${s.folder}|${settingText(s.want)}`));
    const fresh = (g: DarkGroup) => !used.has(`${g.folder}|${settingText(g)}`);
    const all = rows.map((g, i) => ({ g, i }));
    if (check) {
      // Spot checks: the newest group not used yet.
      const best = all.filter(({ g }) => fresh(g)).sort((a, b) => (b.g.last ?? "").localeCompare(a.g.last ?? ""))[0] ?? all[0];
      setPicked(best ? best.i : null);
      return;
    }
    // Sets: the previous set's setting in another folder, else any unused dark group, else the first.
    const darks = all.filter(({ g }) => g.kind !== "bias");
    const best = (previous && darks.find(({ g }) => same(g, previous.want) && fresh(g))) || darks.find(({ g }) => fresh(g)) || darks[0];
    setPicked(best ? best.i : rows.length ? 0 : null);
  }

  async function browse() {
    if (!folder.trim()) return;
    setBusy(true);
    setError(null);
    setGroups(null);
    try {
      const res = await fetch(`/engine/api/leaks/browse?folder=${encodeURIComponent(folder.trim())}`, { cache: "no-store" });
      const body = await res.json();
      if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : `engine returned ${res.status}`);
      choose(body.groups as DarkGroup[]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }

  async function add() {
    if (!groups || picked == null) return;
    const g = groups[picked];
    setBusy(true);
    setError(null);
    try {
      const res = check
        ? await fetch(`/engine/api/leaks/${test.id}/checks`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ probe: pick(g), reference: reference != null ? pick(groups[reference]) : null, comment }),
          })
        : await fetch(`/engine/api/leaks/${test.id}/sets`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...pick(g), comment }),
          });
      const body = await res.json();
      if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : `engine returned ${res.status}`);
      setComment("");
      setGroups(null);
      setPicked(null);
      setReference(null);
      onAdded(body as LeakTest);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }

  const g = groups && picked != null ? groups[picked] : null;
  const mismatch = !check && g && previous && !same(g, previous.want);
  const download = g && g.kind === "light" && (g.inDownload ?? /[\\/]\.Download([\\/]|$)/i.test(g.folder));
  const frames = g ? g.frames + (reference != null && groups ? groups[reference].frames : 0) : 0;

  return (
    <div className={`framed ${styles.add}`}>
      <Marks />
      <div className={styles.row}>
        <label className={`${styles.field} ${styles.grow}`}>
          <span>{check ? "Folder with the flashlight frames" : "Folder with the darks"}</span>
          <input
            className={styles.input}
            value={folder}
            onChange={(e) => setFolder(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void browse();
              }
            }}
            placeholder="A full path, or a path under the data root"
          />
        </label>
        <button type="button" className="button" onClick={browse} disabled={busy || !folder.trim()}>
          <FolderSearch size={13} strokeWidth={1.8} aria-hidden="true" /> Find frames
        </button>
        {library.length ? (
          <label className={styles.field}>
            <span>Or from the calibration library</span>
            <select
              className={styles.select}
              value=""
              onChange={(e) => {
                const lg = library[Number(e.target.value)];
                if (!lg) return;
                setFolder(lg.folder);
                choose([lg]);
              }}
            >
              <option value="">Pick a dark group…</option>
              {library.map((lg, i) => (
                <option key={i} value={i}>
                  {lg.build} · {lg.folder.split(/[\\/]/).slice(-2).join("/")} · {settingText(lg)} · {lg.frames} frames
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>

      {groups ? (
        groups.length ? (
          <div className={styles.groups}>
            <table className="table">
              <thead>
                <tr>
                  <th aria-label="Pick" />
                  <th>Folder</th>
                  <th>Type</th>
                  <th>Setting</th>
                  <th className="num">Frames</th>
                  <th>Taken</th>
                </tr>
              </thead>
              <tbody>
                {groups.map((row, i) => (
                  <tr key={i} className={i === picked ? styles.groupPicked : undefined} onClick={() => setPicked(i)}>
                    <td>
                      <input type="radio" name={`group-${kind}`} checked={i === picked} onChange={() => setPicked(i)} aria-label={`Use ${row.folder}`} />
                    </td>
                    <td className="mono" style={{ fontSize: 11.5, whiteSpace: "normal", overflowWrap: "anywhere" }}>
                      {row.folder.startsWith(folder.trim()) && row.folder !== folder.trim() ? `…${row.folder.slice(folder.trim().length)}` : row.folder}
                    </td>
                    <td>{row.kind}</td>
                    <td>{settingText(row)}</td>
                    <td className="num">{row.frames}</td>
                    <td className="muted">
                      {when(row.first)}
                      {row.last && row.last !== row.first ? ` → ${when(row.last)}` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted" style={{ marginTop: 12 }}>
            No dark, bias or light frames in this folder or the folders below it.
          </p>
        )
      ) : null}

      {g && groups ? (
        <div style={{ marginTop: 14 }}>
          {check ? (
            <label className={styles.field} style={{ marginBottom: 12, maxWidth: 640 }}>
              <span>Frames without the flashlight</span>
              <select className={styles.select} value={reference ?? ""} onChange={(e) => setReference(e.target.value === "" ? null : Number(e.target.value))}>
                <option value="">In the same group — I&apos;ll mark them after the analysis</option>
                {groups.map((row, i) =>
                  i === picked ? null : (
                    <option key={i} value={i}>
                      {row.folder.split(/[\\/]/).slice(-1)[0]} · {row.kind} · {settingText(row)} · {row.frames} frames
                    </option>
                  ),
                )}
              </select>
            </label>
          ) : null}
          {mismatch ? (
            <p style={{ fontSize: 13.5, marginBottom: 10 }}>
              <StatusIcon level="warn" size={13} /> Not the same setting as the previous set ({settingText(previous.want)}): dark
              current and bias differ, so the comparison will mostly show that.
            </p>
          ) : null}
          {!check && g.frames < 6 ? (
            <p style={{ fontSize: 13.5, marginBottom: 10 }}>
              <StatusIcon level="info" size={13} /> {g.frames} frames: 10 or more give a much steadier noise floor.
            </p>
          ) : null}
          {download ? (
            <p style={{ fontSize: 13.5, marginBottom: 10 }}>
              <StatusIcon level="warn" size={13} /> These are LIGHT frames in NINA&apos;s image folder: auto-ingest files lights into a target
              when tonight&apos;s target takes every light of the night. Move them out, or shoot spot checks as DARK.
            </p>
          ) : null}
          <label className={styles.field}>
            <span>{check ? "What is this pass?" : "What changed for this set?"}</span>
            <textarea
              className={styles.textarea}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              maxLength={2000}
              placeholder={
                check
                  ? "e.g. Flashlight tour, room dark, scope capped"
                  : previous
                    ? "e.g. Aluminium foil over the joint of the tube and camera"
                    : "e.g. Baseline: rig as is, room lights on, lens cap on"
              }
            />
          </label>
          <div className={styles.row} style={{ marginTop: 10 }}>
            <button type="button" className="button button-primary" onClick={add} disabled={busy}>
              <Plus size={13} strokeWidth={2} aria-hidden="true" /> {check ? "Add spot check" : previous ? "Add and compare" : "Add as baseline"}
            </button>
            <span className="muted" style={{ fontSize: 13 }}>
              {check ? `${frames} frames, each judged on its own.` : g.frames > 60 ? "60 evenly spaced frames are analysed." : `${g.frames} frames are analysed.`}
            </span>
          </div>
        </div>
      ) : null}
      {error ? <p className={styles.error} style={{ marginTop: 10 }}>{error}</p> : null}
    </div>
  );
}
