// VENDORED from D:\Python\AstroPlanner\scripts\verify-snr.ts (2026-10-09); only the coordinates import path changed.
/**
 * Pins the SNR engine to numbers computed by hand, independently of the code.
 *
 * The reference rig is the RedCat 51 (51mm, unobstructed, 250mm FL) with an
 * IMX585 at 2.9um => 2.393 arcsec/px, through a 3nm filter. QE is pinned flat
 * at 0.80 so the arithmetic below can be checked on paper without having to
 * interpolate a curve.
 */

import assert from "node:assert/strict";

import {
  airmass,
  allocateBudget,
  apertureAreaCm2,
  bortleFromSqm,
  effectiveSkyMag,
  lineSignalRate,
  optimalSubSeconds,
  readNoisePenalty,
  secondsForSnr,
  skyElectronRate,
  skyMagFromRate,
  snrAt,
  type Camera,
  type ExposureParams,
  type FilterSpec,
  type SignalContext,
  type Telescope,
} from "../src/lib/snr/index.js";
import { pixelScaleArcsec } from "../src/lib/astro/coordinates.js";

let checks = 0;
function check(label: string, fn: () => void) {
  fn();
  checks++;
  console.log(`  ok  ${label}`);
}

/** Relative tolerance assertion, so a failure shows both numbers. */
function close(actual: number, expected: number, rel: number, label: string) {
  const diff = Math.abs(actual - expected) / Math.abs(expected);
  assert.ok(
    diff <= rel,
    `${label}: got ${actual.toPrecision(8)}, expected ${expected.toPrecision(8)} (rel diff ${(diff * 100).toFixed(4)}%)`,
  );
}

const redcat: Telescope = {
  id: "redcat51",
  name: "RedCat 51",
  apertureMm: 51,
  obstructionMm: 0,
  focalLengthMm: 250,
  transmission: 0.95,
};

const imx585: Camera = {
  id: "ref",
  name: "reference IMX585",
  sensor: "IMX585",
  pixelSizeUm: 2.9,
  widthPx: 3856,
  heightPx: 2180,
  readNoiseE: 1.2,
  darkCurrentEPerSec: 0.003,
  fullWellE: 39000,
  gainEPerAdu: 0.35,
  // Flat, so the reference arithmetic is checkable by hand.
  qe: [
    { nm: 400, qe: 0.8 },
    { nm: 900, qe: 0.8 },
  ],
};

const ha3: FilterSpec = {
  id: "ha-3",
  name: "H-alpha 3nm",
  centerNm: 656.28,
  fwhmNm: 3,
  peakTransmission: 0.9,
  line: "Ha",
  skyLineFactor: 1,
};

const PIXEL_SCALE = pixelScaleArcsec(2.9, 250);

const ctx: SignalContext = {
  telescope: redcat,
  camera: imx585,
  filter: ha3,
  pixelScale: PIXEL_SCALE,
  altitudeDeg: 90, // airmass 1
};

console.log("\nSNR engine - reference rig: RedCat 51 + IMX585, 3nm, zenith\n");

check("pixel scale is 2.393 arcsec/px", () => {
  close(PIXEL_SCALE, 2.3927, 1e-3, "pixel scale");
});

check("aperture area is 20.428 cm^2", () => {
  close(apertureAreaCm2(redcat), 20.4282, 1e-4, "aperture area");
});

check("airmass at zenith is 1 and stays finite at the horizon", () => {
  close(airmass(90), 1, 1e-9, "airmass(90)");
  assert.ok(airmass(1) < 50, "airmass near the horizon must stay finite");
});

/**
 * Reference A - target signal.
 *   p     = 206.265 . 2.9 / 250 = 2.392674 arcsec/px,  p^2 = 5.7248889
 *   A_tel = pi/4 . 5.1^2          = 20.4282084 cm^2
 *   100 R . 1.87041e-6 . 20.4282084 . 5.7248889 . 0.80 . 0.90 . 0.95 . 10^(-0.04)
 *   = 0.01364557 e-/s/px
 */
const OBJ_RATE_100R = 0.01364557;
check("100 R of H-alpha gives 0.0136456 e-/s/px", () => {
  close(lineSignalRate(ctx, 100), OBJ_RATE_100R, 2e-4, "line signal rate");
});

check("signal is linear in surface brightness", () => {
  close(lineSignalRate(ctx, 250), lineSignalRate(ctx, 100) * 2.5, 1e-9, "linearity");
});

/**
 * Reference B - sky background at Bortle 9.
 *   1005 . 10^(-0.4*18) . 30 A . 20.4282084 . 5.7248889 . 0.80 . 0.90 . 0.95
 *   = 0.1521734 e-/s/px
 *
 * Against a read noise of 1.2 e- (R^2 = 1.44) that is 46 e- of sky in a 300s
 * sub, or 32x read noise: these subs are comfortably sky-limited.
 */
const SKY_RATE_B9 = 0.1521734;
check("SQM 18.0 through a 3nm filter gives 0.1521734 e-/s/px", () => {
  close(skyElectronRate(ctx, 18.0), SKY_RATE_B9, 2e-4, "sky rate");
});

check("sky rate carries no extinction term", () => {
  // Sky glow forms and scatters inside the atmosphere, so it is not attenuated
  // by it the way target light is.
  const low = { ...ctx, altitudeDeg: 30 };
  close(
    skyElectronRate(low, 18.0),
    skyElectronRate(ctx, 18.0),
    1e-12,
    "sky rate vs altitude",
  );
});

check("skyMagFromRate inverts skyElectronRate", () => {
  close(skyMagFromRate(ctx, SKY_RATE_B9), 18.0, 1e-4, "sky mag round trip");
});

check("Bortle labels track SQM", () => {
  assert.equal(bortleFromSqm(17.2).class, 9);
  assert.equal(bortleFromSqm(18.0).class, 7);
  assert.equal(bortleFromSqm(21.8).class, 1);
  assert.equal(bortleFromSqm(20.5).class, 4);
});

const params: ExposureParams = {
  objectRate: OBJ_RATE_100R,
  skyRate: SKY_RATE_B9,
  darkRate: imx585.darkCurrentEPerSec,
  readNoise: imx585.readNoiseE,
  subSeconds: 300,
};

check("SNR is exactly proportional to sqrt(T) at fixed sub length", () => {
  const a = snrAt(params, 3600);
  const b = snrAt(params, 4 * 3600);
  close(b / a, 2, 1e-12, "SNR doubling");
});

check("secondsForSnr inverts snrAt to better than 0.01%", () => {
  for (const target of [5, 20, 50, 100]) {
    const seconds = secondsForSnr(params, target);
    close(snrAt(params, seconds), target, 1e-4, `round trip at SNR ${target}`);
  }
});

check("300s subs at Bortle 9 cost under 3% to read noise", () => {
  const penalty = readNoisePenalty(params);
  assert.ok(penalty < 1.03, `penalty was ${penalty.toFixed(4)}`);
  assert.ok(penalty > 1, "penalty must exceed 1");
});

/**
 * The regime change that matters when you travel: at Bortle 9 a ~93s sub
 * already swamps read noise, while under a dark sky the same filter needs
 * roughly 26 minutes.
 */
check("optimal sub flips from ~93s at SQM 18 to ~26min at SQM 21.5", () => {
  const bright = optimalSubSeconds({
    objectRate: OBJ_RATE_100R,
    skyRate: SKY_RATE_B9,
    darkRate: imx585.darkCurrentEPerSec,
    readNoise: imx585.readNoiseE,
  });
  close(bright, 92.8, 0.02, "optimal sub at SQM 18");
  assert.ok(bright < 300, "300s subs should already be sky-limited at Bortle 9");

  const darkSkyRate = skyElectronRate(ctx, 21.5);
  const dark = optimalSubSeconds({
    objectRate: OBJ_RATE_100R,
    skyRate: darkSkyRate,
    darkRate: imx585.darkCurrentEPerSec,
    readNoise: imx585.readNoiseE,
  });
  assert.ok(dark > 1200, `dark-site optimal sub was ${dark.toFixed(0)}s, expected > 1200s`);
});

check("budget allocation equalises SNR and conserves hours", () => {
  const weak: ExposureParams = { ...params, objectRate: OBJ_RATE_100R * 0.3 };
  const strong: ExposureParams = { ...params, objectRate: OBJ_RATE_100R };

  const split = allocateBudget(
    [
      { id: "oiii", params: weak },
      { id: "ha", params: strong },
    ],
    15,
  );

  const total = split.reduce((acc, s) => acc + s.hours, 0);
  close(total, 15, 1e-9, "budget conservation");
  close(split[0].snr, split[1].snr, 1e-9, "equal SNR");
  assert.ok(split[0].hours > split[1].hours, "the weaker channel must get more time");
});

check("moonlight brightens the sky by a plausible amount", () => {
  const noMoon = effectiveSkyMag(21.5, null);
  close(noMoon, 21.5, 1e-9, "no moon leaves the sky unchanged");

  const belowHorizon = effectiveSkyMag(21.5, {
    illumination: 1,
    altitudeDeg: -10,
    separationDeg: 90,
    targetAltitudeDeg: 60,
  });
  close(belowHorizon, 21.5, 1e-9, "a set moon contributes nothing");

  const fullMoon = effectiveSkyMag(21.5, {
    illumination: 1,
    altitudeDeg: 50,
    separationDeg: 60,
    targetAltitudeDeg: 60,
  });
  assert.ok(
    fullMoon > 17.5 && fullMoon < 20.5,
    `full-moon sky was ${fullMoon.toFixed(2)} mag/arcsec^2, expected 17.5-20.5`,
  );
  assert.ok(fullMoon < noMoon, "the moon must brighten the sky, not darken it");

  // Moving away from the moon helps, but only up to a point. KS91's Rayleigh
  // term goes as (1.06 + cos^2 rho), which is symmetric about 90 degrees and
  // rises again toward 180 as backscatter takes over; the Mie term is what
  // makes small separations so much worse. The minimum sits near 90 degrees,
  // so this is NOT a monotonic function of separation - do not "fix" it into one.
  //
  // Geometry note: with the moon at 50 deg and the target at 60 deg altitude,
  // the separation cannot exceed (90-50) + (90-60) = 70 deg. Test inside that.
  const near = effectiveSkyMag(21.5, {
    illumination: 1,
    altitudeDeg: 50,
    separationDeg: 20,
    targetAltitudeDeg: 60,
  });
  const away = effectiveSkyMag(21.5, {
    illumination: 1,
    altitudeDeg: 50,
    separationDeg: 65,
    targetAltitudeDeg: 60,
  });
  assert.ok(away > near, "moving away from the moon must help over 20 -> 65 deg");
});

/**
 * The headline reason narrowband works from a bright site: at 3nm the sky rate
 * is 200x smaller than through luminance, so the same site costs vastly less
 * integration in narrowband.
 */
check("a 3nm filter sees ~1/100 the sky of luminance", () => {
  const lum: FilterSpec = {
    id: "lum",
    name: "Luminance",
    centerNm: 550,
    fwhmNm: 300,
    peakTransmission: 0.97,
    skyLineFactor: 1,
  };
  const ratio = skyElectronRate({ ...ctx, filter: lum }, 18.0) / skyElectronRate(ctx, 18.0);
  assert.ok(
    ratio > 50 && ratio < 200,
    `luminance/narrowband sky ratio was ${ratio.toFixed(1)}, expected 50-200`,
  );
});

console.log(`\n${checks} checks passed\n`);
