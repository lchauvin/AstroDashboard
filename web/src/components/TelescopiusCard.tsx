import { ExternalLink } from "lucide-react";

import { Marks } from "@/components/ui";
import type { ObjectPayload } from "@/lib/telescopius";
import styles from "./TelescopiusCard.module.css";

const size = (maj: number | null, min: number | null) =>
  maj == null ? null : `${maj < 10 ? maj.toFixed(1) : Math.round(maj)}′${min ? ` × ${min < 10 ? min.toFixed(1) : Math.round(min)}′` : ""}`;

/** Object facts from Telescopius: reference thumbnail, every catalogue ID, size, brightness. Renders nothing without a key. */
export function TelescopiusCard({ payload }: { payload: ObjectPayload | null }) {
  if (!payload?.keySet) return null;
  const o = payload.object;
  if (!o) {
    return <p className={styles.note}>{payload.error ? `Telescopius: ${payload.error}` : "Telescopius does not know this name."}</p>;
  }
  const aliases = [...new Set([...o.ids.filter((i) => i !== o.id), ...o.names.filter((n) => n !== o.name)])];
  const facts: [string, string | null][] = [
    ["Size", size(o.majArcmin, o.minArcmin)],
    ["Surface brightness", o.surfBr != null ? `${o.surfBr} mag/″²` : null],
    ["Magnitude", o.vmag != null ? `${o.vmag} V${o.photoMag != null ? ` · ${o.photoMag} B` : ""}` : null],
    ["Position angle", o.paDeg != null ? `${o.paDeg}°` : null],
    ["Morphology", o.morph],
    ["Constellation", o.conName],
  ];
  return (
    <div className={`framed ${styles.card}`}>
      <Marks />
      {o.thumbUrl ? (
        <a href={o.url ?? o.imageUrl ?? o.thumbUrl} target="_blank" rel="noreferrer" className={styles.thumb}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={o.thumbUrl} alt={`Reference image of ${o.id}`} loading="lazy" />
        </a>
      ) : null}
      <div className={styles.body}>
        <div className={styles.title}>
          {o.id}
          {o.name !== o.id ? <span className={styles.name}>{o.name}</span> : null}
        </div>
        {aliases.length ? <div className={styles.aliases}>{aliases.slice(0, 12).join(" · ")}</div> : null}
        <dl className={styles.facts}>
          {facts
            .filter(([, v]) => v)
            .map(([k, v]) => (
              <div key={k}>
                <dt>{k}</dt>
                <dd className="num">{v}</dd>
              </div>
            ))}
        </dl>
        {o.url ? (
          <a href={o.url} target="_blank" rel="noreferrer" className={styles.link}>
            Telescopius <ExternalLink size={11} aria-hidden />
          </a>
        ) : null}
      </div>
    </div>
  );
}
