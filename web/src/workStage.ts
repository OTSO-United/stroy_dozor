import type { Work } from "./api";

export type WorkPhase = "active" | "completed" | "upcoming" | "overdue";

export function workPhase(
  work: Work,
  state: string | undefined,
  now: number,
): WorkPhase {
  if (state === "completed") return "completed";
  if (state === "in_progress") return "active";
  const startsAt = Date.parse(work.starts_at);
  const endsAt = Date.parse(work.ends_at);
  if (Number.isFinite(startsAt) && Number.isFinite(endsAt)) {
    if (startsAt <= now && now < endsAt) return "active";
    if (endsAt <= now) return "overdue";
  }
  return "upcoming";
}

const codeCollator = new Intl.Collator("ru", {
  numeric: true,
  sensitivity: "base",
});

export function compareWorkByCodeThenDate(left: Work, right: Work): number {
  const byCode = codeCollator.compare(left.code, right.code);
  if (byCode) return byCode;
  const leftStart = Date.parse(left.starts_at);
  const rightStart = Date.parse(right.starts_at);
  const byDate =
    (Number.isFinite(leftStart) ? leftStart : Number.POSITIVE_INFINITY) -
    (Number.isFinite(rightStart) ? rightStart : Number.POSITIVE_INFINITY);
  return (Number.isNaN(byDate) ? 0 : byDate) || left.id.localeCompare(right.id);
}
