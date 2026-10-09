"""SQLite header cache in the dashboard's own var/ directory.

Keyed by (path, size, mtime): a frame is re-read only when it changes, so a
rescan after a new night touches only the new files.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS frames (
    path   TEXT PRIMARY KEY,
    size   INTEGER NOT NULL,
    mtime  REAL NOT NULL,
    header TEXT,
    error  TEXT
);
CREATE TABLE IF NOT EXISTS dismissals (
    key     TEXT PRIMARY KEY,
    kind    TEXT NOT NULL,
    label   TEXT,
    note    TEXT,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS diagnostics (
    revision TEXT NOT NULL,
    session  TEXT NOT NULL,
    finished REAL NOT NULL,
    duration REAL NOT NULL,
    summary  TEXT NOT NULL,
    result   TEXT NOT NULL,
    PRIMARY KEY (revision, session)
);
CREATE TABLE IF NOT EXISTS sites (
    id      TEXT PRIMARY KEY,
    data    TEXT NOT NULL,
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS goals (
    revision TEXT PRIMARY KEY,
    data     TEXT NOT NULL,
    updated  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ingest_journal (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             REAL NOT NULL,
    download_night TEXT NOT NULL,
    op             TEXT NOT NULL,
    src            TEXT NOT NULL,
    dst            TEXT NOT NULL,
    size           INTEGER,
    revision       TEXT,
    kind           TEXT,
    filter         TEXT,
    undone         REAL,
    error          TEXT
);
CREATE TABLE IF NOT EXISTS object_map (
    object      TEXT PRIMARY KEY,
    display     TEXT NOT NULL,
    revision_id TEXT,
    catalog_id  TEXT,
    created     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class HeaderCache:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._lock = threading.Lock()

    def load_all(self) -> dict[str, tuple[int, float, dict[str, Any] | None, str | None]]:
        with self._lock:
            rows = self._conn.execute("SELECT path, size, mtime, header, error FROM frames").fetchall()
        return {
            p: (s, m, json.loads(h) if h else None, e)
            for p, s, m, h, e in rows
        }

    def upsert(self, rows: Iterable[tuple[str, int, float, dict[str, Any] | None, str | None]]) -> None:
        payload = [(p, s, m, json.dumps(h) if h is not None else None, e) for p, s, m, h, e in rows]
        if not payload:
            return
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT INTO frames(path, size, mtime, header, error) VALUES (?,?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, "
                "header=excluded.header, error=excluded.error",
                payload,
            )

    def forget(self, paths: Iterable[str]) -> None:
        """Drop cache rows for files that no longer exist (cache only; never the files)."""
        payload = [(p,) for p in paths]
        if not payload:
            return
        with self._lock, self._conn:
            self._conn.executemany("DELETE FROM frames WHERE path = ?", payload)

    def dismissals(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT key, kind, label, note, created FROM dismissals").fetchall()
        return {k: {"key": k, "kind": kind, "label": label, "note": note, "created": created} for k, kind, label, note, created in rows}

    def dismiss(self, key: str, kind: str, label: str | None, note: str | None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO dismissals(key, kind, label, note, created) VALUES (?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET label=excluded.label, note=excluded.note",
                (key, kind, label, note, time.time()),
            )

    def undismiss(self, key: str) -> bool:
        with self._lock, self._conn:
            return self._conn.execute("DELETE FROM dismissals WHERE key = ?", (key,)).rowcount > 0

    def save_diagnostic(self, revision: str, session: str, duration: float, summary: dict, result: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO diagnostics(revision, session, finished, duration, summary, result) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(revision, session) DO UPDATE SET finished=excluded.finished, duration=excluded.duration, "
                "summary=excluded.summary, result=excluded.result",
                (revision, session, time.time(), duration, json.dumps(summary), json.dumps(result)),
            )

    def diagnostic_summaries(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT revision, session, finished, duration, summary FROM diagnostics").fetchall()
        return [
            {"revisionId": r, "session": s, "finished": f, "duration": d, "summary": json.loads(j)}
            for r, s, f, d, j in rows
        ]

    def diagnostic(self, revision: str, session: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT finished, duration, result FROM diagnostics WHERE revision = ? AND session = ?",
                (revision, session),
            ).fetchone()
        if not row:
            return None
        return {"finished": row[0], "duration": row[1], "result": json.loads(row[2])}

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def get_json(self, key: str) -> Any:
        raw = self.get_meta(key)
        return json.loads(raw) if raw else None

    def set_json(self, key: str, value: Any) -> None:
        self.set_meta(key, json.dumps(value))

    # ------------------------------------------------------------------ planning

    def sites(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT id, data, created, updated FROM sites ORDER BY created").fetchall()
        return [{**json.loads(d), "id": i, "created": c, "updated": u} for i, d, c, u in rows]

    def save_site(self, site_id: str, data: dict[str, Any]) -> None:
        body = {k: v for k, v in data.items() if k not in ("id", "created", "updated")}
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO sites(id, data, created, updated) VALUES (?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated=excluded.updated",
                (site_id, json.dumps(body), now, now),
            )

    def delete_site(self, site_id: str) -> bool:
        """Removes a site record from the dashboard database (never touches the data root)."""
        with self._lock, self._conn:
            return self._conn.execute("DELETE FROM sites WHERE id = ?", (site_id,)).rowcount > 0

    def goals(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT revision, data FROM goals").fetchall()
        return {r: json.loads(d) for r, d in rows}

    def save_goals(self, revision: str, goals: dict[str, Any]) -> None:
        with self._lock, self._conn:
            if goals:
                self._conn.execute(
                    "INSERT INTO goals(revision, data, updated) VALUES (?,?,?) "
                    "ON CONFLICT(revision) DO UPDATE SET data=excluded.data, updated=excluded.updated",
                    (revision, json.dumps(goals), time.time()),
                )
            else:
                self._conn.execute("DELETE FROM goals WHERE revision = ?", (revision,))

    # ------------------------------------------------------------------ ingest

    def journal_add(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT INTO ingest_journal(ts, download_night, op, src, dst, size, revision, kind, filter, error) "
                "VALUES (:ts, :download_night, :op, :src, :dst, :size, :revision, :kind, :filter, :error)",
                rows,
            )

    def journal(self, download_night: str | None = None, limit: int = 5000) -> list[dict[str, Any]]:
        q = "SELECT id, ts, download_night, op, src, dst, size, revision, kind, filter, undone, error FROM ingest_journal"
        args: tuple = ()
        if download_night:
            q += " WHERE download_night = ?"
            args = (download_night,)
        q += " ORDER BY id DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(q, args + (limit,)).fetchall()
        keys = ("id", "ts", "downloadNight", "op", "src", "dst", "size", "revision", "kind", "filter", "undone", "error")
        return [dict(zip(keys, r)) for r in rows]

    def journal_mark_undone(self, ids: list[int]) -> None:
        with self._lock, self._conn:
            self._conn.executemany("UPDATE ingest_journal SET undone = ? WHERE id = ?", [(time.time(), i) for i in ids])

    def object_map(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT object, display, revision_id, catalog_id, created FROM object_map").fetchall()
        return {o: {"object": d, "revisionId": r, "catalogId": c, "created": t} for o, d, r, c, t in rows}

    def map_object(self, display: str, revision_id: str | None, catalog_id: str | None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO object_map(object, display, revision_id, catalog_id, created) VALUES (?,?,?,?,?) "
                "ON CONFLICT(object) DO UPDATE SET display=excluded.display, revision_id=excluded.revision_id, catalog_id=excluded.catalog_id",
                (display.casefold(), display, revision_id, catalog_id, time.time()),
            )

    def unmap_object(self, display: str) -> bool:
        """Forget a mapping row in the dashboard database (no files involved)."""
        with self._lock, self._conn:
            return self._conn.execute("DELETE FROM object_map WHERE object = ?", (display.casefold(),)).rowcount > 0
