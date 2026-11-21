"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import styles from "./settings.module.css";

/** One folder target folders are found in, as the engine's last scan saw it (`GET /api/library/roots`). */
export type LibraryRoot = {
  path: string;
  archive: boolean;
  exists: boolean;
  /** Target folders read from it; null before the first scan with it. */
  targets: number | null;
  /** Target folders skipped because an earlier folder has one of the same name. */
  skipped: string[];
};

async function send(path: string, method: string, body?: unknown) {
  const res = await fetch(`/engine${path}`, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : `Engine error ${res.status}`);
  return data;
}

const norm = (p: string) => p.trim().replace(/^"|"$/g, "").replace(/[\\/]+$/, "").toLowerCase();

/** Settings → Library: the data root, plus archive folders (e.g. finished targets moved to another disk) whose target
 *  folders are also shown in the Library. */
export function LibrarySettings({ archiveDirs, roots }: { archiveDirs: string[]; roots: LibraryRoot[] | null }) {
  const router = useRouter();
  const [dirs, setDirs] = useState(archiveDirs);
  const [adding, setAdding] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const dataRoot = roots?.find((r) => !r.archive) ?? null;
  const status = (dir: string) => roots?.find((r) => r.archive && norm(r.path) === norm(dir)) ?? null;
  const changed = dirs.length !== archiveDirs.length || dirs.some((d, i) => d !== archiveDirs[i]);

  const add = () => {
    const d = adding.trim().replace(/^"|"$/g, "");
    if (!d) return;
    if (dirs.some((x) => norm(x) === norm(d))) {
      setError(`${d} is in the list already.`);
      return;
    }
    setError(null);
    setDirs([...dirs, d]);
    setAdding("");
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    setMsg(null);
    try {
      const saved = await send("/api/settings", "PUT", { library: { archiveDirs: dirs } });
      setDirs(saved?.settings?.library?.archiveDirs ?? []);
      setMsg("Saved. The Library is rescanning; folder counts below update once it is done (reload this page).");
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.rig}>
      <h3 className="eyebrow">Target folders</h3>
      <p className={styles.hint} style={{ marginTop: 6 }}>
        The Library lists every target folder found directly under these folders. Move finished targets to another disk and
        add that disk&apos;s folder here to keep them in the Library, with their nights, diagnostics and goals. Move a target
        folder as a whole and keep its name: diagnostics and goals follow the folder name, not the disk.
      </p>
      <p className={styles.hint} style={{ marginTop: 6 }}>
        Archive folders are read only. The organizer, quarantine and auto-ingest never change anything there, and the
        calibration library (<code>.Calibration</code>) and NINA&apos;s image folder stay on the data root. To add nights
        to an archived target, move it back first or start a new revision. If a target folder is in two places (while
        it is being copied, say), the first folder in this list wins.
      </p>

      <table className="table" style={{ marginTop: 14 }}>
        <thead>
          <tr>
            <th>Folder</th>
            <th>Targets</th>
            <th aria-label="Actions" />
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>
              <span className="mono">{dataRoot?.path ?? "data root"}</span>
              <div className={styles.hint}>Data root, set in astrodashboard.toml ([data] root)</div>
              <RootProblems root={dataRoot} />
            </td>
            <td className="mono">{dataRoot?.targets ?? "–"}</td>
            <td />
          </tr>
          {dirs.map((d, i) => {
            const st = status(d);
            return (
              <tr key={d}>
                <td>
                  <span className="mono">{d}</span>
                  <div className={styles.hint}>Archive{st ? "" : " · not scanned yet (save to scan it)"}</div>
                  <RootProblems root={st} />
                </td>
                <td className="mono">{st?.targets ?? "–"}</td>
                <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                  <button type="button" className="button" disabled={i === 0} onClick={() => setDirs([...dirs.slice(0, i - 1), d, dirs[i - 1], ...dirs.slice(i + 1)])} aria-label={`Move ${d} up`}>
                    ↑
                  </button>{" "}
                  <button type="button" className="button" onClick={() => setDirs(dirs.filter((_, j) => j !== i))}>
                    Remove
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <div className={styles.rowActions} style={{ marginTop: 14 }}>
        <label className={styles.field} style={{ minWidth: 360, flex: 1 }}>
          <span>Add an archive folder</span>
          <input
            className={styles.input}
            value={adding}
            onChange={(e) => setAdding(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                add();
              }
            }}
            placeholder="H:\Astro"
            spellCheck={false}
          />
        </label>
        <button type="button" className="button" style={{ alignSelf: "flex-end" }} disabled={!adding.trim()} onClick={add}>
          Add
        </button>
      </div>
      <div className={styles.rowActions} style={{ marginTop: 14 }}>
        <button type="button" className="button button-primary" disabled={!changed || busy} onClick={save}>
          {busy ? "Saving…" : "Save and rescan"}
        </button>
        {changed ? <span className={styles.hint}>Unsaved changes</span> : null}
      </div>
      {msg ? <p className={styles.ok} style={{ marginTop: 10 }}>{msg}</p> : null}
      {error ? <p className={styles.error} style={{ marginTop: 10 }}>{error}</p> : null}
    </div>
  );
}

function RootProblems({ root }: { root: LibraryRoot | null }) {
  if (!root) return null;
  return (
    <>
      {!root.exists ? (
        <div className={styles.warn}>Not found: is the disk plugged in? Its targets are hidden from the Library until it is back.</div>
      ) : null}
      {root.skipped.length ? (
        <div className={styles.warn}>
          Skipped, already found in a folder above: {root.skipped.join(", ")}
        </div>
      ) : null}
    </>
  );
}
