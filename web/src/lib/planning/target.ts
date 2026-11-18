/** What Planning knows about a target from the catalog, and the filters that suit its class. */

import type { ObjectClass } from "@/lib/snr";
import type { FilterLetter } from "./rig";

export type CatalogInfo = {
  name: string;
  ra: number;
  dec: number;
  majAxArcmin: number | null;
  minAxArcmin: number | null;
  paDeg: number | null;
  type: string;
  vmag: number | null;
  surfBr: number | null;
  commonName: string | null;
  objectClass: ObjectClass;
};

/** The filters that make sense for a class when the target has no data or goals yet. */
export function classFilters(cls: ObjectClass, owned: FilterLetter[]): FilterLetter[] {
  const want: FilterLetter[] =
    cls === "galaxy" || cls === "reflection" || cls === "cluster" || cls === "dark" || cls === "star"
      ? ["L", "R", "G", "B"]
      : cls === "planetary"
        ? ["O", "H", "S"]
        : ["H", "O", "S"];
  const have = want.filter((f) => owned.includes(f));
  return have.length ? have : want;
}
