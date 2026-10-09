/**
 * Pins the dashboard's fast planner (src/lib/planning) to the vendored
 * AstroPlanner modules it approximates — "tonight's ranking agrees with
 * AstroPlanner for the same inputs" (PLAN §8, Phase 4).
 */

import assert from "node:assert/strict";
import { Horizon, Observer } from "astronomy-engine";

import { nightWindow, summariseNight } from "../src/lib/astro/ephemeris.js";
import { moonSkyBrightnessNl, skyElectronRate } from "../src/lib/snr/index.js";
import { altAz, horizonAt, nightGrid, usableHours } from "../src/lib/planning/night.js";
import { buildRig } from "../src/lib/planning/rig.js";
import { filterSky, moonlightNl, skyRate } from "../src/lib/planning/skymodel.js";
import { goalStatus } from "../src/lib/planning/plan.js";
import { parseLatLon } from "../src/lib/planning/sites.js";
import { readFrameSite } from "../src/lib/planning/frameHeader.js";
import type { Site } from "../src/lib/planning/types.js";

let checks = 0;
function check(label: string, fn: () => void) {
  fn();
  checks++;
  console.log(`  ok  ${label}`);
}

const home: Site = { id: "home", name: "Home", lat: 45.5442, lon: -73.5375, elevation: null, sqm: 18, minAlt: 20, horizon: [], isDefault: true, notes: "" };
const NGC7000 = { ra: 314.821, dec: 44.529 };
const M31 = { ra: 10.685, dec: 41.269 };

console.log("\nPlanner vs. vendored AstroPlanner ephemeris and sky model\n");

check("fast alt/az matches astronomy-engine within 0.6° (unprecessed J2000)", () => {
  const obs = new Observer(home.lat, home.lon, 0);
  const g = nightGrid(home, "2026-10-15", 30);
  for (const s of g.samples) {
    for (const t of [NGC7000, M31]) {
      const ref = Horizon(new Date(s.t), obs, t.ra / 15, t.dec);
      const got = altAz(s.lst, t.ra, t.dec, home.lat);
      assert.ok(Math.abs(got.alt - ref.altitude) < 0.6, `alt ${got.alt} vs ${ref.altitude}`);
      const daz = Math.abs(((got.az - ref.azimuth + 540) % 360) - 180);
      if (ref.altitude < 85) assert.ok(daz < 1.5, `az ${got.az} vs ${ref.azimuth}`);
    }
  }
});

check("darkness window equals ephemeris.nightWindow within a minute", () => {
  for (const date of ["2026-01-15", "2026-06-21", "2026-10-15"]) {
    const g = nightGrid(home, date, 10);
    const ref = nightWindow({ latitude: home.lat, longitude: home.lon, elevationM: 0 }, new Date(`${date}T12:00:00`));
    assert.ok(ref && g.dusk && g.dawn, `${date}: both must find darkness`);
    assert.ok(Math.abs(g.dusk - ref.duskUtc.getTime()) < 60_000, `${date} dusk`);
    assert.ok(Math.abs(g.dawn - ref.dawnUtc.getTime()) < 60_000, `${date} dawn`);
  }
});

check("usable hours agree with ephemeris.summariseNight within 20 minutes", () => {
  for (const date of ["2026-08-15", "2026-10-15", "2026-12-15"]) {
    for (const t of [NGC7000, M31]) {
      const ref = summariseNight({ latitude: home.lat, longitude: home.lon, elevationM: 0 }, t, new Date(`${date}T12:00:00`), { minAltitudeDeg: 30, siteSqm: 18 });
      const got = usableHours(nightGrid(home, date, 10, false), { ...home, minAlt: 30 }, t.ra, t.dec);
      assert.ok(Math.abs(got - ref.usableHours) <= 0.34, `${date} ${t.ra}: ${got} vs ${ref.usableHours}`);
    }
  }
});

check("polar summer: no darkness, zero samples (not an error)", () => {
  const g = nightGrid({ ...home, lat: 69.6 }, "2026-06-21", 10);
  assert.equal(g.dusk, null);
  assert.equal(g.samples.length, 0);
});

check("moonlight split sums to the vendored KS91 value", () => {
  for (const sep of [10, 45, 90, 150]) {
    const m = { illumination: 0.7, altitudeDeg: 35, separationDeg: sep, targetAltitudeDeg: 55 };
    const split = moonlightNl(m);
    const ref = moonSkyBrightnessNl(m);
    assert.ok(Math.abs(split.rayleigh + split.mie - ref) / ref < 1e-9);
  }
});

const rig = buildRig(
  { instrume: "QHYminiCam8M", telescop: "RedCat 51", focalLength: 250, pixelSizeUm: 2.9, widthPx: 3856, heightPx: 2180, frames: 1, filters: ["L", "R", "G", "B", "H", "O", "S"], subSeconds: {} },
  null,
);

check("headers map to the RedCat 51 + Minicam8M presets", () => {
  assert.equal(rig.telescope.id, "redcat51");
  assert.equal(rig.camera.id, "minicam8m");
  assert.ok(Math.abs(rig.pixelScale - 2.3927) < 1e-3);
});

check("dark-sky rate equals the vendored skyElectronRate for κ = 1", () => {
  for (const letter of ["L", "H", "O"] as const) {
    const f = rig.filters[letter]!;
    const fs = filterSky(rig, f, 18.0, 1);
    const ref = skyElectronRate({ telescope: rig.telescope, camera: rig.camera, filter: f, pixelScale: rig.pixelScale, altitudeDeg: 90 }, 18.0);
    assert.ok(Math.abs(fs.darkRate - ref) / ref < 1e-9, `${letter}: ${fs.darkRate} vs ${ref}`);
  }
});

check("moonlight costs OIII more than Hα (bluer scattered light)", () => {
  const moon = moonlightNl({ illumination: 0.8, altitudeDeg: 40, separationDeg: 60, targetAltitudeDeg: 50 });
  const ratio = (l: "H" | "O") => {
    const fs = filterSky(rig, rig.filters[l]!, 18.0, 1);
    return skyRate(fs, moon) / fs.darkRate - 1; // moonlight added, relative to the dark sky
  };
  assert.ok(ratio("O") > ratio("H") * 2, `O +${ratio("O")} vs H +${ratio("H")}`);
});

check("horizon profile interpolates and wraps through north", () => {
  const site = { minAlt: 20, horizon: [{ az: 10, alt: 30 }, { az: 200, alt: 40 }, { az: 350, alt: 10 }] };
  assert.equal(horizonAt(site, 105), 35);
  assert.equal(horizonAt(site, 0), 20); // 10 + (30-10)/2 = 20, floor 20
  assert.equal(horizonAt({ minAlt: 20, horizon: [] }, 123), 20);
});

check("SNR goal → hours → SNR round trip; cap flag", () => {
  const rate = 25; // SNR² per hour
  const g = goalStatus("O", { mode: "snr", snr: 30 }, 3600 * 4, 3600 * 5, rate, 40, null);
  assert.ok(Math.abs(g.goalHours! - 36) < 1e-9 && Math.abs(g.remainingHours! - 32) < 1e-9);
  assert.ok(Math.abs(g.snrSoFar! - 10) < 1e-9);
  const h = goalStatus("O", { mode: "hours", hours: 36 }, 0, 0, rate, 40, null);
  assert.ok(Math.abs(h.goalSnr! - 30) < 1e-9);
  assert.ok(goalStatus("O", { mode: "snr", snr: 60 }, 0, 0, rate, 40, null).flag?.includes("cap"));
});

check("lat/long paste formats", () => {
  assert.deepEqual(parseLatLon("45.544, -73.538"), { lat: 45.544, lon: -73.538 });
  assert.deepEqual(parseLatLon("https://www.google.com/maps/@45.4553,-71.1525,15z"), { lat: 45.4553, lon: -71.1525 });
  assert.equal(parseLatLon("somewhere"), null);
});

/** In-memory File stand-in: the parser only uses slice().arrayBuffer() / text(). */
const memFile = (bytes: Uint8Array) =>
  ({
    slice: (a: number, b: number) => {
      const part = bytes.slice(a, b);
      return { arrayBuffer: async () => part.buffer, text: async () => new TextDecoder("latin1").decode(part) };
    },
  }) as unknown as File;
const card = (k: string, v: string) => `${k.padEnd(8)}= ${v}`.padEnd(80).slice(0, 80);

const fits = new TextEncoder().encode(
  [card("SIMPLE", "T".padStart(20)), card("SITELAT", "'45 32 39'"), card("SITELONG", "-73.5375 / east positive"), card("SITEELEV", "0.0"), card("OBJECT", "'Soul Nebula'"), "END".padEnd(80)].join("").padEnd(2880),
);
const xml = `<?xml version="1.0"?><xisf version="1.0"><Image><FITSKeyword name="OBJECT" value="'M 31'" comment=""/><Property id="Observation:Location:Latitude" type="Float64" value="45.455"/><Property id="Observation:Location:Longitude" type="Float64" value="288.848"/><Property id="Observation:Location:Elevation" type="Float64" value="1111"/></Image></xisf>`;
const xmlBytes = new TextEncoder().encode(xml);
const xisf = new Uint8Array(16 + xmlBytes.length);
xisf.set(new TextEncoder().encode("XISF0100"), 0);
new DataView(xisf.buffer).setUint32(8, xmlBytes.length, true);
xisf.set(xmlBytes, 16);

const headerCases = [
  { label: "FITS header: sexagesimal latitude, zero elevation = unset", bytes: fits, lat: 45.544167, lon: -73.5375, elevation: null, object: "Soul Nebula" },
  { label: "XISF header: native properties, 0–360 longitude folded", bytes: xisf, lat: 45.455, lon: -71.152, elevation: 1111, object: "M 31" },
];
for (const c of headerCases) {
  const got = await readFrameSite(memFile(c.bytes));
  check(c.label, () => {
    assert.ok(Math.abs(got.lat! - c.lat) < 1e-5 && Math.abs(got.lon! - c.lon) < 1e-5, JSON.stringify(got));
    assert.equal(got.elevation, c.elevation);
    assert.equal(got.object, c.object);
  });
}

console.log(`\n${checks} checks passed\n`);
