import "server-only";

import fs from "node:fs";
import path from "node:path";

import { classifyType } from "@/lib/snr";
import type { CatalogInfo } from "@/lib/planning/signal";

/**
 * The deep-sky catalog, VENDORED from D:\Python\AstroPlanner\catalog\targets.json (2026-10-09):
 * NGC/IC from OpenNGC (CC-BY-SA-4.0) plus Sharpless, LBN and LDN from VizieR.
 * Read from disk once per process so 1.2 MB never enters a bundle.
 * `normaliseDesignation` is vendored from AstroPlanner src/server/catalog.ts.
 */

type Row = [string, number, number, number | null, number | null, number | null, string, number | null, number | null, number | null, string | null];
type CatalogFile = { generated: string; sources: string[]; aliases: Record<string, string>; objects: Row[] };

const CATALOG_PATH = path.join(process.cwd(), "data", "catalog", "targets.json");

let cache: { file: CatalogFile; index: Map<string, number> } | null = null;

function load() {
  if (cache) return cache;
  const file = JSON.parse(fs.readFileSync(CATALOG_PATH, "utf8")) as CatalogFile;
  const index = new Map<string, number>();
  file.objects.forEach((row, i) => {
    index.set(row[0].toLowerCase(), i);
    if (row[10] && !index.has(row[10].toLowerCase())) index.set(row[10].toLowerCase(), i);
  });
  for (const [alias, canonical] of Object.entries(file.aliases)) {
    const i = index.get(canonical.toLowerCase());
    if (i !== undefined && !index.has(alias.toLowerCase())) index.set(alias.toLowerCase(), i);
  }
  cache = { file, index };
  return cache;
}

export const catalogSources = () => load().file.sources;

function toInfo(row: Row): CatalogInfo {
  return {
    name: row[0],
    ra: row[1],
    dec: row[2],
    majAxArcmin: row[3],
    minAxArcmin: row[4],
    paDeg: row[5],
    type: row[6],
    vmag: row[7],
    surfBr: row[9],
    commonName: row[10],
    objectClass: classifyType(row[6], row[0]),
  };
}

export function normaliseDesignation(input: string): string {
  const t = input.trim().replace(/\s+/g, " ");
  const sharpless = /^sh\s*-?\s*2\s*-?\s*(\d+)$/i.exec(t);
  if (sharpless) return `Sh2-${Number(sharpless[1])}`;
  const prefixed = /^(ngc|ic|m|lbn|ldn|messier)\s*-?\s*(\d+)\s*([a-z]?)$/i.exec(t);
  if (prefixed) {
    const raw = prefixed[1].toLowerCase();
    const prefix = raw === "messier" || raw === "m" ? "M" : raw.toUpperCase();
    return `${prefix} ${Number(prefixed[2])}${prefixed[3] ? prefixed[3].toUpperCase() : ""}`;
  }
  return t;
}

/** Exact lookup by designation, alias or common name; tries the catalog id first, then the common name. */
export function findCatalog(...queries: (string | null | undefined)[]): CatalogInfo | null {
  const { file, index } = load();
  for (const q of queries) {
    if (!q) continue;
    for (const key of [normaliseDesignation(q), q.trim()]) {
      const i = index.get(key.toLowerCase());
      if (i !== undefined) return toInfo(file.objects[i]);
    }
  }
  return null;
}

let ideas: CatalogInfo[] | null = null;

/**
 * Candidate "new target" ideas: objects a small refractor can frame and that
 * the SNR model can say something about. LBN/LDN entries duplicate the
 * Sharpless and NGC ones and dark nebulae have no emission to model, so both are left out.
 */
export function ideaCandidates(): CatalogInfo[] {
  if (ideas) return ideas;
  const { file } = load();
  ideas = file.objects
    .filter((r) => !/^(LBN|LDN)\b/.test(r[0]))
    .map(toInfo)
    .filter((t) => {
      const size = t.majAxArcmin ?? 0;
      switch (t.objectClass) {
        case "hii":
        case "snr":
        case "wolfrayet":
          return size >= 10;
        case "planetary":
          return size >= 3;
        case "reflection":
          return size >= 10;
        case "galaxy":
          return (t.vmag ?? 99) <= 10 && size >= 6;
        default:
          return false;
      }
    });
  return ideas;
}
