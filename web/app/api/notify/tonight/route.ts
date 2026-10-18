import { NextResponse } from "next/server";

import { checkTonight } from "@/server/notify";

export const dynamic = "force-dynamic";

/** Tonight's notification without sending it: what the phone would get, and whether it is due. */
export async function GET() {
  return respond(await checkTonight("preview"));
}

/**
 * Called by the scheduled task (scripts/notify-tonight.ps1): sends tonight's verdict when it is due.
 * `?test=1` sends it regardless (Settings → Notifications → Send test).
 */
export async function POST(request: Request) {
  const test = new URL(request.url).searchParams.get("test") === "1";
  try {
    return respond(await checkTonight(test ? "test" : "check"));
  } catch (e) {
    return NextResponse.json({ error: e instanceof Error ? e.message : String(e) }, { status: 502 });
  }
}

function respond(r: Awaited<ReturnType<typeof checkTonight>>) {
  return r.ok ? NextResponse.json(r.result) : NextResponse.json({ error: r.error }, { status: r.status });
}
