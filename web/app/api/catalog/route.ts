import { NextResponse } from "next/server";

import { findCatalog, normaliseDesignation } from "@/server/catalog";

/** Designation lookup for forms: `?q=ngc7000` → the catalog id and common name, or `{ found: false }`. */
export function GET(request: Request) {
  const q = new URL(request.url).searchParams.get("q")?.slice(0, 80) ?? "";
  const hit = q.trim() ? findCatalog(q) : null;
  if (!hit) return NextResponse.json({ found: false, catalogId: normaliseDesignation(q) });
  return NextResponse.json({ found: true, catalogId: hit.name, name: hit.commonName, type: hit.type });
}
