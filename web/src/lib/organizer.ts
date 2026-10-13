/** Organizer payloads (engine/astrodash/api_organizer.py). */

export type OrgOp = {
  op: "move" | "mkdir" | "rmdir_empty" | "trash" | "write_new" | "repoint";
  src?: string;
  dst?: string;
  kind?: "dir" | "file";
  why?: string;
  text?: string;
  /** repoint: PixInsight project references that change. */
  refs?: number;
};

export type Lint = { code: string; level: "info" | "warn"; text: string };

export type OrgRevision = {
  revisionId: string;
  folder: string;
  catalogId: string;
  stage: string;
  lint: Lint[];
  counts: Record<string, number>;
  warnings: string[];
  conflicts: string[];
  canonical: boolean;
};

export type PlanStatus = "draft" | "blocked" | "applying" | "applied" | "partial" | "failed" | "discarded";

export type OrgPlanSummary = {
  id: string;
  created: number;
  revisionId: string | null;
  title: string;
  status: PlanStatus;
  counts?: Record<string, number>;
  result?: { done: number[]; error: string | null } | null;
};

export type OrgPlan = OrgPlanSummary & {
  target: string;
  ops: OrgOp[];
  warnings: string[];
  conflicts: string[];
  lint: Lint[];
  undoOf?: string;
  dataRoot?: string;
};

export type Trash = { path: string; plans: { plan: string; bytes: number; files: number }[]; bytes: number; files: number };

export type OrganizerOverview = { revisions: OrgRevision[]; plans: OrgPlanSummary[]; trash: Trash; dataRoot?: string };

export const OP_LABEL: Record<OrgOp["op"], string> = {
  move: "Move",
  mkdir: "New folder",
  rmdir_empty: "Remove empty folder",
  trash: "To trash",
  write_new: "New file",
  repoint: "Re-point project",
};

export const bytes = (n: number) =>
  n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : n >= 1e3 ? `${(n / 1e3).toFixed(0)} kB` : `${n} B`;

/** Mirrors target_name_problems in engine/astrodash/organizer/plan.py; the engine has the final word. */
export function targetNameProblem(catalogId: string, name: string): string | null {
  for (const [label, v] of [["Catalog id", catalogId], ["Name", name]] as const) {
    if (!v.trim()) return `${label} is empty`;
    // eslint-disable-next-line no-control-regex
    if (/[<>:"/\\|?*()[\]\x00-\x1f]/.test(v)) return `${label} cannot contain < > : " / \\ | ? * ( ) [ ]`;
    if (v.trim().endsWith(".")) return `${label} cannot end with a dot`;
  }
  if (/\sR\d+$/.test(name.trim())) return 'Name cannot end in " R<n>": that suffix marks a revision';
  return null;
}
