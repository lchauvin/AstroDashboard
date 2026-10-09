import { Marks } from "./ui";

/** Sections that arrive in later phases (PLAN.md §9). */
export function Placeholder({
  eyebrow,
  title,
  phase,
  items,
}: {
  eyebrow: string;
  title: string;
  phase: string;
  items: string[];
}) {
  return (
    <main className="page">
      <div className="eyebrow">{eyebrow}</div>
      <h1
        style={{
          fontSize: 46,
          lineHeight: 0.96,
          letterSpacing: "0.04em",
          textTransform: "uppercase",
          marginTop: 8,
        }}
      >
        {title}
      </h1>
      <div className="framed" style={{ padding: "22px 26px", maxWidth: 680, marginTop: 28 }}>
        <Marks />
        <div className="eyebrow">{phase}</div>
        <ul style={{ margin: "12px 0 0", paddingLeft: 18, lineHeight: 1.75, color: "var(--text-secondary)" }}>
          {items.map((i) => (
            <li key={i}>{i}</li>
          ))}
        </ul>
      </div>
    </main>
  );
}
