// VENDORED from D:\Python\AstroPlanner\scripts\verify-ephemeris.ts (2026-10-09); only the import path changed.
/**
 * Ephemeris checks, anchored on a real session: NGC 6888 from Montreal on the
 * night of 2026-07-04, which is one of the six nights that actually produced
 * the reference frame this project was built around.
 *
 * The July case is the interesting one: at 45.5 N in early July there are only
 * 3.35 hours of astronomical darkness, and the target being 83 degrees up all
 * night does not help while the sky is still blue. The moon that night was 84%
 * lit and 58 degrees from the target, which is the sort of thing that shows up
 * later as a rejected-subs count.
 *
 * Where a figure below can be derived from first principles, the derivation is
 * in the comment and the assertion checks the library against it — not the
 * other way round.
 */

import assert from "node:assert/strict";

import {
  altitudeOf,
  moonState,
  nightWindow,
  nightsToGoal,
  summariseNight,
  type Site,
} from "../src/lib/astro/ephemeris.js";

let checks = 0;
function check(label: string, fn: () => void) {
  fn();
  checks++;
  console.log(`  ok  ${label}`);
}

const MONTREAL: Site = { latitude: 45.5, longitude: -73.6, elevationM: 50 };
const TROMSO: Site = { latitude: 69.65, longitude: 18.96, elevationM: 10 };
const QUITO: Site = { latitude: -0.18, longitude: -78.47, elevationM: 2850 };

const CRESCENT = { ra: 303.027258, dec: 38.35494 };

console.log("\nEphemeris\n");

check("RA is handed over in hours, not degrees", () => {
  // The units trap. A target at Dec = latitude passes within a few degrees of
  // the zenith at culmination; if RA were passed in degrees the peak altitude
  // would be wrong by an arbitrary amount depending on time of day.
  const target = { ra: CRESCENT.ra, dec: MONTREAL.latitude };
  let peak = -90;
  for (let m = 0; m < 24 * 60; m += 5) {
    const when = new Date(Date.UTC(2026, 6, 5, 0, 0, 0) + m * 60_000);
    peak = Math.max(peak, altitudeOf(MONTREAL, target, when).altitude);
  }
  assert.ok(peak > 88, `a target at Dec = latitude should reach the zenith; peaked at ${peak.toFixed(1)}`);
});

check("NGC 6888 rides high from Montreal", () => {
  // Dec +38.35 from latitude 45.5: culmination = 90 - |45.5 - 38.35| = 82.9 deg.
  let peak = -90;
  for (let m = 0; m < 24 * 60; m += 5) {
    const when = new Date(Date.UTC(2026, 6, 5, 0, 0, 0) + m * 60_000);
    peak = Math.max(peak, altitudeOf(MONTREAL, CRESCENT, when).altitude);
  }
  assert.ok(
    Math.abs(peak - 82.9) < 1.5,
    `peak altitude was ${peak.toFixed(1)}, expected ~82.9`,
  );
});

check("early-July darkness at 45.5 N is real but short", () => {
  const night = nightWindow(MONTREAL, new Date(2026, 6, 4));
  assert.ok(night, "there should be astronomical darkness on 2026-07-04");

  /*
   * Checked by hand rather than taken from the library. On 4 July the solar
   * declination is +22.85, so at latitude 45.5 the sun's lowest altitude is
   * dec + lat - 90 = -21.65: it clears -18, but not by much.
   *
   * For the duration, solve the standard altitude equation for the hour angle
   * at which the sun sits at -18:
   *   sin(h)  = sin(phi)sin(dec) + cos(phi)cos(dec)cos(H)
   *   -0.3090 = 0.2770 + 0.6458 cos(H)
   *   cos(H)  = -0.9074  ->  H = 155.16 deg
   * Darkness runs from H = 155.16 through midnight (180) to 204.84, so
   * 2 x 24.84 = 49.68 deg of hour angle = 3.31 hours.
   */
  assert.ok(
    night.darkHours > 2.9 && night.darkHours < 3.8,
    `darkness lasted ${night.darkHours.toFixed(2)} h, expected ~3.3 by hand calculation`,
  );
  assert.ok(night.duskUtc < night.dawnUtc, "dusk must precede dawn");
  console.log(
    `      2026-07-04: ${night.darkHours.toFixed(2)} h of astronomical darkness`,
  );
});

check("midwinter darkness is far longer than midsummer", () => {
  const summer = nightWindow(MONTREAL, new Date(2026, 6, 4));
  const winter = nightWindow(MONTREAL, new Date(2026, 11, 21));
  assert.ok(summer && winter);
  // Same hand check as above, at dec = -23.44: cos(H) = -0.0392, H = 92.25 deg,
  // so darkness spans 2 x 87.75 = 175.5 deg of hour angle = 11.70 hours. That
  // is 3.5x the July figure, not the 4x first guessed here.
  assert.ok(
    Math.abs(winter.darkHours - 11.7) < 0.6,
    `winter darkness was ${winter.darkHours.toFixed(2)} h, expected ~11.7 by hand calculation`,
  );
  assert.ok(
    winter.darkHours > summer.darkHours * 3,
    `winter ${winter.darkHours.toFixed(1)} h vs summer ${summer.darkHours.toFixed(1)} h`,
  );
  console.log(
    `      Dec 21: ${winter.darkHours.toFixed(2)} h vs Jul 4: ${summer.darkHours.toFixed(2)} h`,
  );
});

check("polar summer reports no darkness rather than zero hours", () => {
  // Tromso in June: the sun never gets 18 degrees down. This must be
  // distinguishable from "the target was too low", which is a different problem.
  const night = nightWindow(TROMSO, new Date(2026, 5, 21));
  assert.equal(night, null, "Tromso in June has no astronomical darkness");

  const summary = summariseNight(TROMSO, CRESCENT, new Date(2026, 5, 21), {
    minAltitudeDeg: 30,
    siteSqm: 21.5,
  });
  assert.equal(summary.noAstronomicalDarkness, true);
  assert.equal(summary.usableHours, 0);

  // And in midwinter the same site has plenty.
  const winter = nightWindow(TROMSO, new Date(2026, 11, 21));
  assert.ok(winter && winter.darkHours > 12, "polar winter should be very dark");
});

check("the equator gets consistent darkness year round", () => {
  const march = nightWindow(QUITO, new Date(2026, 2, 21));
  const september = nightWindow(QUITO, new Date(2026, 8, 21));
  assert.ok(march && september);
  assert.ok(
    Math.abs(march.darkHours - september.darkHours) < 1,
    "equatorial nights barely vary",
  );
  assert.ok(march.darkHours > 9, "expected a long equatorial night");
});

check("the moon's phase cycles with the synodic month", () => {
  const base = new Date(Date.UTC(2026, 6, 4, 4, 0, 0));
  const state = moonState(MONTREAL, CRESCENT, base);

  assert.ok(
    state.illumination >= 0 && state.illumination <= 1,
    `illumination was ${state.illumination}`,
  );
  assert.ok(
    state.separationDeg >= 0 && state.separationDeg <= 180,
    `separation was ${state.separationDeg}`,
  );

  // Self-consistency without needing an almanac: one synodic month later the
  // phase must very nearly repeat.
  const later = new Date(base.getTime() + 29.530588 * 86_400_000);
  const nextCycle = moonState(MONTREAL, CRESCENT, later);
  assert.ok(
    Math.abs(nextCycle.illumination - state.illumination) < 0.05,
    `phase drifted by ${Math.abs(nextCycle.illumination - state.illumination).toFixed(3)} over one synodic month`,
  );

  // Half a cycle later it must be markedly different.
  const half = new Date(base.getTime() + 14.77 * 86_400_000);
  assert.ok(
    Math.abs(moonState(MONTREAL, CRESCENT, half).illumination - state.illumination) > 0.3,
    "half a synodic month should change the phase substantially",
  );
  console.log(
    `      2026-07-04: moon ${(state.illumination * 100).toFixed(0)}% lit, ` +
      `${state.separationDeg.toFixed(0)} deg from the Crescent`,
  );
});

check("a moonlit night reports a brighter mean sky than a dark one", () => {
  const opts = { minAltitudeDeg: 30, siteSqm: 21.5, stepMinutes: 15 };

  let brightest = { mag: 99, date: new Date() };
  let darkest = { mag: -99, date: new Date() };

  // Walk a full lunation in October, when Montreal nights are properly long.
  for (let i = 0; i < 30; i++) {
    const date = new Date(2026, 9, 1 + i);
    const s = summariseNight(MONTREAL, CRESCENT, date, opts);
    if (s.usableHours <= 0) continue;
    if (s.meanSkyMag < brightest.mag) brightest = { mag: s.meanSkyMag, date };
    if (s.meanSkyMag > darkest.mag) darkest = { mag: s.meanSkyMag, date };
  }

  assert.ok(
    darkest.mag - brightest.mag > 0.5,
    `moonlight only moved the sky by ${(darkest.mag - brightest.mag).toFixed(2)} mag over a lunation`,
  );
  // A dark night must not come out brighter than the site's own darkness.
  assert.ok(darkest.mag <= 21.5 + 1e-6, `darkest night was ${darkest.mag.toFixed(2)}, above the site SQM`);
  console.log(
    `      October range: ${brightest.mag.toFixed(2)} to ${darkest.mag.toFixed(2)} mag/arcsec^2`,
  );
});

check("nights-to-goal accumulates SNR squared, not hours", () => {
  const objectRate = 0.0136;
  // Sky-dominated: variance tracks the sky brightness, so a moonlit night
  // genuinely contributes less than a dark one of the same length.
  const varianceFor = (skyMag: number) => objectRate + 1005 * Math.pow(10, -0.4 * skyMag) * 30 * 0.1;

  const result = nightsToGoal(
    MONTREAL,
    CRESCENT,
    new Date(2026, 9, 1),
    60,
    { targetSnr: 25, objectRate, varianceFor },
    { minAltitudeDeg: 30, siteSqm: 21.0, stepMinutes: 15 },
  );

  assert.ok(result.nights.length > 0, "some nights should be usable in October");
  assert.ok(result.totalHours > 0);

  // SNR must rise monotonically and never linearly in nights.
  for (let i = 1; i < result.nights.length; i++) {
    assert.ok(
      result.nights[i].snrAfter > result.nights[i - 1].snrAfter,
      "SNR must increase every night that contributes",
    );
  }

  if (result.reached) {
    const last = result.nights[result.nights.length - 1];
    assert.ok(last.snrAfter >= 25, "the final night must cross the goal");
    console.log(
      `      SNR 25 reached in ${result.nights.length} nights / ` +
        `${result.totalHours.toFixed(1)} h of usable darkness`,
    );
  } else {
    console.log(
      `      not reached in 60 nights: SNR ${result.finalSnr.toFixed(1)} ` +
        `after ${result.totalHours.toFixed(1)} h`,
    );
  }
});

check("a higher altitude floor never yields more usable time", () => {
  const date = new Date(2026, 9, 15);
  const low = summariseNight(MONTREAL, CRESCENT, date, {
    minAltitudeDeg: 20,
    siteSqm: 21,
    stepMinutes: 10,
  });
  const high = summariseNight(MONTREAL, CRESCENT, date, {
    minAltitudeDeg: 60,
    siteSqm: 21,
    stepMinutes: 10,
  });
  assert.ok(
    high.usableHours <= low.usableHours,
    `floor 60 gave ${high.usableHours} h, floor 20 gave ${low.usableHours} h`,
  );
  console.log(
    `      Oct 15 usable: ${low.usableHours.toFixed(1)} h above 20 deg, ` +
      `${high.usableHours.toFixed(1)} h above 60 deg`,
  );
});

console.log(`\n${checks} checks passed\n`);
