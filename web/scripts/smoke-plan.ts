/** Dev smoke test: plan tonight against the running engine and print the ranking and timings. */
import { buildRig } from "../src/lib/planning/rig.js";
import { calibrateSites } from "../src/lib/planning/calibrate.js";
import { plan, halphaKey } from "../src/lib/planning/plan.js";
import { findCatalog, ideaCandidates } from "../src/server/catalog.js";
import { defaultSite } from "../src/lib/planning/sites.js";
import type { PlanningContext, SkyPayload } from "../src/lib/planning/types.js";

const E = process.env.ASTRODASH_ENGINE_URL ?? "http://127.0.0.1:3007";
const ctx = (await (await fetch(`${E}/api/planning/context`)).json()) as PlanningContext;
const sky = (await (await fetch(`${E}/api/planning/sky`)).json()) as SkyPayload;
const rig = buildRig(ctx.detectedRig, ctx.settings.rig);
const site = defaultSite(ctx.sites);
let t = Date.now();
const cal = calibrateSites(sky.samples, ctx.sites, rig);
console.log("calibration", Date.now() - t, "ms", JSON.stringify(cal[site.id], null, 0));
const halpha: Record<string, any> = {};
for (const tg of ctx.targets) {
  const info = findCatalog(tg.catalogId, tg.name);
  if (!info) continue;
  const r = await fetch(`${E}/api/planning/halpha?ra=${info.ra}&dec=${info.dec}&maj=${info.majAxArcmin ?? ""}&min=${info.minAxArcmin ?? ""}&pa=${info.paDeg ?? ""}`);
  halpha[halphaKey(info.ra, info.dec)] = r.ok ? await r.json() : null;
}
const date = process.argv[2] ?? new Date().toISOString().slice(0, 10);
for (let i = 0; i < 2; i++) {
  t = Date.now();
  const res = plan({ site, date, rig, targets: ctx.targets, catalogFor: (x) => findCatalog(x.catalogId, x.name), ideas: ideaCandidates(), halpha, calibration: cal[site.id], goalCapHours: ctx.settings.goalCapHours });
  console.log(`plan #${i}: ${Date.now() - t} ms (inner ${res.computeMs}), ideas pool ${ideaCandidates().length}`);
  if (i === 1) {
    console.log("tonight", new Date(res.tonight.dusk!).toISOString(), "→", new Date(res.tonight.dawn!).toISOString(), "moon", res.tonight.moonIllum.toFixed(2), "moon-free h", res.tonight.moonFreeHours);
    for (const p of res.yours) console.log(p.title.padEnd(12), p.target?.stage.padEnd(10), "h", p.tonightHours.toFixed(1), "score", p.score.toFixed(2), p.best?.letter ?? "-", "|", p.advice, "| anchor", p.anchor.basis, p.anchor.rayleighs.toFixed(0), "| goals", p.goals.map((g) => `${g.letter}:${g.goodHours.toFixed(1)}h`).join(" "));
    for (const p of res.ideas.slice(0, 12)) console.log("idea", p.title.padEnd(10), (p.subtitle ?? "").padEnd(24), p.classLabel.padEnd(18), "h", p.tonightHours.toFixed(1), "90d", p.hours90.toFixed(0), "score", p.score.toFixed(2), p.best?.letter, p.advice);
    for (const c of res.calendar.slice(0, 10)) console.log(c.date, "moon", c.moonIllum.toFixed(2), "dark", c.darkHours.toFixed(1), "moonfree", c.moonFreeHours.toFixed(1), c.best ? `${c.best.title} ${c.best.letter} ${c.best.hours.toFixed(1)}h eff ${c.best.efficiency.toFixed(2)}` : "-");
  }
}
