import type { Job, Project, Work } from "./api";

export type NoticeEvent = {
  key: string;
  type:
    | "analysis_started"
    | "analysis_ready"
    | "analysis_failed"
    | "analysis_cancelled"
    | "saved"
    | "error";
  projectId: string;
  projectName: string;
  runId?: string;
  sourceId?: string;
  work?: Pick<Work, "id" | "code" | "title">;
  message?: string;
};
export type NoticeGroup = {
  id: number;
  type: NoticeEvent["type"];
  events: NoticeEvent[];
  expiresAt: number | null;
};
export type NoticeQueue = {
  visible: NoticeGroup[];
  pending: NoticeGroup[];
  nextId: number;
};
export const emptyNotices = (): NoticeQueue => ({
  visible: [],
  pending: [],
  nextId: 1,
});
export const MAX_VISIBLE = 3;
export const MAX_PENDING = 3;
export const noticePriority = (type: NoticeEvent["type"]) =>
  type === "analysis_failed" || type === "error"
    ? 2
    : type === "analysis_cancelled"
      ? 1
      : 0;
const lifetime = (group: NoticeGroup) =>
  noticePriority(group.type) === 2 ? 12000 : 7000;

// A group's first display deadline never moves, including after preemption.
export function advanceNotices(state: NoticeQueue, now: number): NoticeQueue {
  const alive = (group: NoticeGroup) =>
    group.expiresAt === null || group.expiresAt > now;
  const visible = state.visible.filter(alive);
  const pending = state.pending
    .filter(alive)
    .sort(
      (a, b) => noticePriority(b.type) - noticePriority(a.type) || a.id - b.id,
    );
  while (visible.length < MAX_VISIBLE && pending.length) {
    const group = pending.shift()!;
    visible.push({
      ...group,
      expiresAt: group.expiresAt ?? now + lifetime(group),
    });
  }
  return { ...state, visible, pending };
}

export function enqueueNotices(
  state: NoticeQueue,
  events: NoticeEvent[],
  now: number,
): NoticeQueue {
  let next = advanceNotices(state, now);
  for (const event of events) {
    const all = [...next.visible, ...next.pending];
    if (
      all.some((group) => group.events.some((item) => item.key === event.key))
    )
      continue;
    const group = all.find((item) => item.type === event.type);
    if (group) {
      const merge = (items: NoticeGroup[]) =>
        items.map((item) =>
          item.id === group.id
            ? { ...item, events: [...item.events, event] }
            : item,
        );
      next = {
        ...next,
        visible: merge(next.visible),
        pending: merge(next.pending),
      };
      continue;
    }
    const added: NoticeGroup = {
      id: next.nextId,
      type: event.type,
      events: [event],
      expiresAt: null,
    };
    next = {
      ...next,
      nextId: next.nextId + 1,
      visible: [...next.visible],
      pending: [...next.pending, added],
    };
    if (next.visible.length === MAX_VISIBLE) {
      const victim = [...next.visible].sort(
        (a, b) =>
          noticePriority(a.type) - noticePriority(b.type) || a.id - b.id,
      )[0];
      if (noticePriority(added.type) > noticePriority(victim.type)) {
        next.visible = next.visible.filter((item) => item.id !== victim.id);
        next.pending.push(victim);
      }
    }
    next = advanceNotices(next, now);
    // Overflow keeps higher priority, then oldest waiting groups. Dropped events
    // remain acknowledged by polling; results stay available in their project.
    next.pending = next.pending.slice(0, MAX_PENDING);
  }
  return next;
}

export function dismissNotice(state: NoticeQueue, id: number, now: number) {
  return advanceNotices(
    {
      ...state,
      visible: state.visible.filter((g) => g.id !== id),
      pending: state.pending.filter((g) => g.id !== id),
    },
    now,
  );
}

export function noticeText(group: NoticeGroup) {
  const n = group.events.length;
  const stages = n % 10 === 1 && n % 100 !== 11 ? "этапа" : "этапов";
  const titles = {
    analysis_started: `Начат анализ ${n} ${stages}`,
    analysis_ready: `Завершён анализ ${n} ${stages}`,
    analysis_failed: `Ошибка анализа ${n} ${stages}`,
    analysis_cancelled: `Отменён анализ ${n} ${stages}`,
    saved: n === 1 ? "Изменения сохранены" : `Сохранено изменений: ${n}`,
    error: n === 1 ? "Не удалось выполнить действие" : `Ошибок действий: ${n}`,
  };
  const projects = new Map(
    group.events.map((e) => [e.projectId, e.projectName]),
  );
  const first = group.events[0];
  const subject =
    projects.size > 1
      ? `${projects.size} объекта: ${[...projects.values()].slice(0, 2).join(", ")}${projects.size > 2 ? "…" : ""}`
      : first.projectName;
  const detail =
    n === 1
      ? first.message ||
        (first.work && `${first.work.code} · ${first.work.title}`)
      : `${n} событий`;
  return {
    title: titles[group.type],
    summary: [subject, detail].filter(Boolean).join(" · "),
  };
}

export function analysisNotices(
  jobs: Job[],
  seen: Set<string>,
  since: number,
  project: Pick<Project, "id" | "name">,
) {
  const events: NoticeEvent[] = [];
  for (const job of [...jobs].reverse()) {
    if (job.kind !== "analyze" || !job.works?.length) continue;
    const jobKey = JSON.stringify(["job", project.id, job.id]);
    const alreadyObserved = seen.has(jobKey);
    const started = job.started_at ? Date.parse(job.started_at) : NaN;
    const transitions: NoticeEvent["type"][] = [];
    if (Number.isFinite(started)) transitions.push("analysis_started");
    if (job.status === "succeeded" && job.results_ready && !job.continuous)
      transitions.push("analysis_ready");
    if (job.status === "failed") transitions.push("analysis_failed");
    if (job.status === "cancelled") transitions.push("analysis_cancelled");
    for (const type of transitions) {
      const occurred =
        type === "analysis_failed" || type === "analysis_cancelled"
          ? Date.parse(job.updated_at)
          : started;
      for (const work of job.works) {
        const key = JSON.stringify([type, project.id, job.id, work.id]);
        if (seen.has(key)) continue;
        seen.add(key);
        if (
          (Number.isFinite(occurred) && occurred >= since) ||
          (alreadyObserved && type !== "analysis_started")
        )
          events.push({
            key,
            type,
            projectId: project.id,
            projectName: project.name,
            runId: job.id,
            sourceId: job.source_id,
            work,
            message:
              type === "analysis_failed" ? job.error || undefined : undefined,
          });
      }
    }
    seen.add(jobKey);
  }
  return events;
}
