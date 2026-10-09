/**
 * Read a site from a FITS or XISF frame in the browser, header only (PLAN §6.1).
 *
 * The file is never uploaded: only the first bytes are read with File.slice —
 * a FITS header is a run of 80-character cards ending with END, an XISF file
 * starts with "XISF0100", a header length, and an XML header carrying
 * FITSKeyword elements. Mirrors engine/astrodash/planning.py site_from_header.
 */

const LAT_KEYS = ["SITELAT", "LAT-OBS", "OBSGEO-B"];
const LON_KEYS = ["SITELONG", "LONG-OBS", "OBSGEO-L"];
const ELEV_KEYS = ["SITEELEV", "ALT-OBS", "OBSGEO-H"];

export type FrameSite = {
  lat: number | null;
  lon: number | null;
  elevation: number | null;
  keys: { lat: string | null; lon: string | null; elevation: string | null };
  object: string | null;
  dateLoc: string | null;
  notes: string[];
  format: "FITS" | "XISF";
};

/** Decimal degrees from "45.544", "45 32 39", "+45:32:39", "73 32 15 W". */
export function parseAngle(v: string | number | null | undefined): number | null {
  if (v == null) return null;
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  const t = v.trim().replace(/^'+|'+$/g, "").trim();
  if (/^[+-]?\d+(\.\d+)?([eE][+-]?\d+)?$/.test(t)) return Number(t);
  const m = /^([+-]?)\s*(\d{1,3})[\s:°d]+(\d{1,2})(?:[\s:'′m]+(\d{1,2}(?:\.\d+)?))?\s*["″s]?\s*([NSEWnsew])?$/.exec(t);
  if (!m) return null;
  let deg = Number(m[2]) + Number(m[3]) / 60 + (m[4] ? Number(m[4]) : 0) / 3600;
  if (m[1] === "-" || (m[5] && "SWsw".includes(m[5]))) deg = -deg;
  return deg;
}

function parseFitsCards(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (let i = 0; i + 80 <= text.length; i += 80) {
    const card = text.slice(i, i + 80);
    const key = card.slice(0, 8).trim();
    if (key === "END") break;
    if (card.slice(8, 10) !== "= ") continue;
    let value = card.slice(10);
    const q = /^\s*'((?:[^']|'')*)'/.exec(value);
    if (q) value = q[1].replace(/''/g, "'").trim();
    else value = value.split("/")[0].trim();
    out[key] = value;
  }
  return out;
}

const XML_ENTITIES: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" };
const unescapeXml = (s: string) => s.replace(/&(amp|lt|gt|quot|apos);/g, (_, e: string) => XML_ENTITIES[e]);

/** Attributes of every <tag …> element; a regex reader so it runs in the browser and in Node alike. */
function elements(xml: string, tag: string): Record<string, string>[] {
  const out: Record<string, string>[] = [];
  for (const m of xml.matchAll(new RegExp(`<${tag}\\b([^>]*?)/?>`, "g"))) {
    const attrs: Record<string, string> = {};
    for (const a of m[1].matchAll(/([\w:-]+)\s*=\s*("([^"]*)"|'([^']*)')/g)) attrs[a[1]] = unescapeXml(a[3] ?? a[4] ?? "");
    out.push(attrs);
  }
  return out;
}

function parseXisfKeywords(xml: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const el of elements(xml, "FITSKeyword")) {
    const { name, value } = el;
    if (name && value != null && !(name in out)) out[name] = value.replace(/^'+|'+$/g, "").trim();
  }
  // XISF native properties, when the FITS keywords are absent.
  for (const el of elements(xml, "Property")) {
    const id = el.id;
    const value = el.value;
    if (!value) continue;
    if (id === "Observation:Location:Latitude" && !out.SITELAT) out.SITELAT = value;
    if (id === "Observation:Location:Longitude" && !out.SITELONG) out.SITELONG = value;
    if (id === "Observation:Location:Elevation" && !out.SITEELEV) out.SITEELEV = value;
    if (id === "Observation:Object:Name" && !out.OBJECT) out.OBJECT = value;
  }
  return out;
}

const first = (h: Record<string, string>, keys: string[]): [number | null, string | null] => {
  for (const k of keys) {
    const v = parseAngle(h[k]);
    if (v != null) return [v, k];
  }
  return [null, null];
};

export async function readFrameSite(file: File): Promise<FrameSite> {
  const head = new Uint8Array(await file.slice(0, 16).arrayBuffer());
  const magic = new TextDecoder("ascii").decode(head.slice(0, 8));
  let header: Record<string, string>;
  let format: FrameSite["format"];
  if (magic === "XISF0100") {
    const len = new DataView(head.buffer).getUint32(8, true);
    if (!len || len > 8_000_000) throw new Error("This XISF header length is not plausible.");
    const xml = await file.slice(16, 16 + len).text();
    header = parseXisfKeywords(xml);
    format = "XISF";
  } else if (magic.startsWith("SIMPLE")) {
    // Headers are usually a few 2880-byte blocks; 64 KB covers ~800 cards.
    header = parseFitsCards(await file.slice(0, 65_536).text());
    format = "FITS";
  } else {
    throw new Error("Not a FITS or XISF file.");
  }
  const notes: string[] = [];
  let [lat, latKey] = first(header, LAT_KEYS);
  let [lon, lonKey] = first(header, LON_KEYS);
  let [elev, elevKey] = first(header, ELEV_KEYS);
  if (elev === 0) {
    notes.push(`${elevKey} is 0, which usually means the capture software did not know it; elevation left empty.`);
    elev = null;
  }
  if (lat != null && Math.abs(lat) > 90) {
    notes.push(`${latKey} = ${lat} is not a latitude.`);
    lat = null;
    latKey = null;
  }
  if (lon != null && lon > 180) lon -= 360;
  if (lat == null || lon == null) notes.push("No site coordinates in this header (looked for SITELAT/SITELONG, LAT-OBS/LONG-OBS, OBSGEO-B/L).");
  return {
    lat,
    lon,
    elevation: elev,
    keys: { lat: latKey, lon: lonKey, elevation: elevKey },
    object: header.OBJECT ?? null,
    dateLoc: header["DATE-LOC"] ?? header["DATE-OBS"] ?? null,
    notes,
    format,
  };
}
