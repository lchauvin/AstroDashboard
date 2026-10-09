import { FirstScanProgress } from "./ScanControls";
import { Marks } from "./ui";

/** Replaces page content when the engine is offline, still on its first scan, or erroring. */
export function EngineNotice({ reason, message }: { reason: string; message: string }) {
  return (
    <div className="framed" style={{ padding: "26px 28px", maxWidth: 640, marginTop: 24 }}>
      <Marks />
      <div className="eyebrow">{reason === "scanning" ? "Indexing" : "Engine"}</div>
      <h2 className="section-title" style={{ margin: "10px 0 12px" }}>
        {reason === "scanning" ? "First scan in progress" : reason === "offline" ? "Engine offline" : "Something went wrong"}
      </h2>
      <p style={{ marginBottom: 10 }}>{message}</p>
      {reason === "offline" ? (
        <p className="muted" style={{ fontSize: 14.5 }}>
          Start it from <span className="mono">D:\Python\AstroDashboard\engine</span> with{" "}
          <span className="mono">uv run astrodash-engine</span>.
        </p>
      ) : null}
      {reason === "scanning" ? <FirstScanProgress /> : null}
    </div>
  );
}
