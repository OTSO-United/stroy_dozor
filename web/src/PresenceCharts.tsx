import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  api,
  duration,
  type Catalog,
  type Monitor,
  type Observation,
  type Plan,
  type Point,
  type Project,
} from "./api";
import { InfoButton } from "./InfoButton";
import {
  EquipmentTimeline,
  type EquipmentTimelineRow,
} from "./EquipmentTimeline";
import { workPhase } from "./workStage";
import { CameraFrameViewer } from "./CameraFrameViewer";
import { defaultSmoothingStep, SMOOTHING_STEPS } from "./chartSmoothing";

const colors = [
  "#196550",
  "#d16b25",
  "#5b61c5",
  "#bc3b73",
  "#1687a0",
  "#a07914",
  "#8f4ba2",
  "#5a841c",
  "#c24b42",
  "#3e70b6",
  "#167776",
  "#a55c2a",
  "#7355a7",
  "#9a423c",
  "#2c7c9e",
  "#789128",
  "#6255bf",
  "#ad557c",
  "#836327",
  "#486f89",
  "#93468a",
  "#427a38",
  "#aa5a48",
  "#536477",
  "#626b1e",
  "#633d25",
  "#0e455e",
  "#664140",
];
export const equipmentColor = (id: string | number) =>
  colors[Number(id) % colors.length] || colors[0];
type Line = {
  id: string;
  name: string;
  color: string;
  values: (number | null)[];
};
type SeriesPage = {
  points: Point[];
  truncated: boolean;
  next_before: string | null;
  next_before_sample_index: number | null;
  next_before_id: string | null;
};
const exactDate = (value: string) =>
  new Intl.DateTimeFormat("ru-RU", {
    timeZone: "Europe/Moscow",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
const timeLabel = (p: Point) =>
  p.time ? exactDate(p.time) : duration(p.offset_seconds);
const clock = (p: Point) =>
  p.time ? Date.parse(p.time) / 1000 : p.offset_seconds;

function Composition({
  lines,
  point,
  planned,
}: {
  lines: Line[];
  point: Point | undefined;
  planned: Record<string, number>;
}) {
  const entries = lines.map((l) => ({
    ...l,
    count: l.values.at(-1) ?? null,
    required: planned[l.id] || 0,
  }));
  const anyKnown = entries.some((e) => e.count !== null);
  const known = entries.length > 0 && entries.every((e) => e.count !== null);
  const required = entries.reduce((sum, e) => sum + e.required, 0);
  const covered = entries.reduce(
    (sum, e) => sum + Math.min(e.count ?? 0, e.required),
    0,
  );
  const coverage = required ? Math.round((covered / required) * 100) : 0;
  return (
    <section className="panel composition-panel">
      <div className="panel-help-heading">
        <h2>Состав техники</h2>
        <InfoButton title="Как читать состав">
          <p>
            Сравнение требований выбранного этапа с последним измерением
            источника. Неизвестный результат не считается нулём.
          </p>
        </InfoButton>
      </div>
      <p className="composition-time">
        {point ? timeLabel(point) : "Ожидаем измерения"}
      </p>
      <div className="composition-body">
        <div
          className="donut"
          role="img"
          aria-label={
            anyKnown
              ? `Наблюдается не менее ${covered} из ${required} машин по плану`
              : "Нет данных о составе техники"
          }
          style={{
            background: anyKnown
              ? `conic-gradient(#2d8a66 0 ${coverage}%, #e5ece8 ${coverage}% 100%)`
              : "#e5ece8",
          }}
        >
          <div>
            <strong>{anyKnown ? `${covered}/${required}` : "-"}</strong>
            <span>
              {known ? "по плану" : anyKnown ? "частично" : "нет данных"}
            </span>
          </div>
        </div>
        <ul className="composition-legend">
          {entries.map((e) => (
            <li key={e.id}>
              <i style={{ background: e.color }} />
              <span>{e.name}</span>
              <strong>
                {e.count === null ? "?" : e.count} / {e.required}
              </strong>
              <small
                className={
                  e.count !== null && e.count < e.required
                    ? "composition-deficit"
                    : ""
                }
              >
                {e.count === null
                  ? "нет данных"
                  : e.count < e.required
                    ? `не хватает ${e.required - e.count}`
                    : e.count > e.required
                      ? `сверх плана ${e.count - e.required}`
                      : "по плану"}
              </small>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function AbsenceHistogram({
  points,
  lines,
}: {
  points: Point[];
  lines: Line[];
}) {
  const summaries = useMemo(
    () =>
      lines.map((line) => {
        const seconds = { present: 0, absent: 0, unknown: 0 };
        const samples = { present: 0, absent: 0, unknown: 0 };
        points.forEach((point, index) => {
          const value = line.values[index];
          const measured = point.presence_seconds_by_class?.[line.id];
          if (measured) {
            seconds.present +=
              measured.present +
              (value !== null && value > 0 ? measured.pending : 0);
            seconds.absent += measured.absent;
            seconds.unknown +=
              measured.unknown +
              (value === null || value === 0 ? measured.pending : 0);
          }
          if (value !== null && value > 0) samples.present++;
          else if (point.presence?.[line.id] === "absent") samples.absent++;
          else samples.unknown++;
        });
        const hasDuration = Object.values(seconds).some((value) => value > 0);
        const totals = hasDuration ? seconds : samples;
        const total = Object.values(totals).reduce(
          (sum, value) => sum + value,
          0,
        );
        const measured = totals.present + totals.absent;
        return {
          ...line,
          totals,
          total,
          hasDuration,
          absenceRatio: measured ? totals.absent / measured : null,
        };
      }),
    [points, lines],
  );
  if (!points.length)
    return (
      <div className="chart-no-data">
        Измерений пока нет. Сводка появится после обработки кадров.
      </div>
    );
  return (
    <div className="absence-histogram" aria-label="Сводка присутствия">
      <div className="absence-histogram-legend">
        <span className="present">В кадре</span>
        <span className="absent">
          Не обнаружено / подтверждённо отсутствует
        </span>
        <span className="unknown">Нет данных</span>
      </div>
      {summaries.map((summary) => (
        <div className="absence-histogram-row" key={summary.id}>
          <div>
            <strong>{summary.name}</strong>
            <span>
              {summary.absenceRatio === null
                ? "Нет подтверждённых измерений"
                : `${Math.round(summary.absenceRatio * 100)}% отсутствия · ${summary.hasDuration ? "по времени" : "по кадрам"}`}
            </span>
          </div>
          <div
            className="absence-histogram-bar"
            title={
              summary.hasDuration
                ? `Отсутствие: ${duration(summary.totals.absent)}; присутствие: ${duration(summary.totals.present)}`
                : `Кадры: присутствие ${summary.totals.present}, отсутствие ${summary.totals.absent}, нет данных ${summary.totals.unknown}`
            }
          >
            {(["present", "absent", "unknown"] as const).map(
              (state) =>
                summary.total > 0 &&
                summary.totals[state] > 0 && (
                  <i
                    key={state}
                    className={state}
                    style={{
                      width: `${(summary.totals[state] / summary.total) * 100}%`,
                    }}
                  />
                ),
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function ActivitySummary({
  points,
  classes,
}: {
  points: Point[];
  classes: EquipmentTimelineRow[];
}) {
  const rows = classes.map((line) => {
    let working = 0;
    let idle = 0;
    for (const point of points) {
      const state = point.activity_seconds_by_class?.[line.id];
      if (state) {
        idle += state.idle ?? 0;
        working += state.working ?? 0;
      }
    }
    const sampleTotals = { working: 0, idle: 0, unknown: 0 };
    points.forEach((point, index) => {
      const count = line.values[index];
      if (count === null) return;
      const measured = point.display_activity_by_class?.[line.id];
      const active = Math.min(count, measured?.working ?? 0);
      const inactive = Math.min(count - active, measured?.idle ?? 0);
      sampleTotals.working += active;
      sampleTotals.idle += inactive;
      sampleTotals.unknown += count - active - inactive;
    });
    const total = working + idle;
    return {
      ...line,
      working,
      idle,
      total,
      displayed: total ? { working, idle, unknown: 0 } : sampleTotals,
      displayedTotal:
        total ||
        Object.values(sampleTotals).reduce((sum, value) => sum + value, 0),
    };
  });
  return (
    <div className="activity-summary" aria-label="Сводка активности по времени">
      <div className="absence-histogram-legend">
        <span className="present">Работает</span>
        <span className="absent">Простаивает</span>
        <span className="unknown">Не определено</span>
      </div>
      {rows.map((row) => (
        <div className="absence-histogram-row" key={row.id}>
          <div>
            <strong>{row.name}</strong>
            <span>
              {row.total
                ? `Машино-время: простой ${duration(row.idle)} · работа ${duration(row.working)}`
                : row.displayedTotal
                  ? `${points.length} измерений`
                  : "Нет измерений"}
            </span>
          </div>
          <div className="absence-histogram-bar" aria-hidden="true">
            {row.displayedTotal > 0 &&
              (["working", "idle", "unknown"] as const).map((state) =>
                row.displayed[state] > 0 ? (
                  <i
                    key={state}
                    className={
                      state === "working"
                        ? "present"
                        : state === "idle"
                          ? "absent"
                          : "unknown"
                    }
                    style={{
                      width:
                        String(
                          (row.displayed[state] / row.displayedTotal) * 100,
                        ) + "%",
                    }}
                  />
                ) : null,
              )}
          </div>
        </div>
      ))}
    </div>
  );
}

function AdditionalSummary({ rows }: { rows: EquipmentTimelineRow[] }) {
  return (
    <div
      className="additional-summary"
      aria-label="Сводка дополнительной техники"
    >
      {rows.map((row) => {
        const maximum = row.values.reduce<number>(
          (largest, value) => Math.max(largest, value ?? 0),
          0,
        );
        return (
          <div className="additional-summary-row" key={row.id}>
            <i style={{ background: row.color }} />
            <strong>{row.name}</strong>
            <span>Сейчас: {row.values.at(-1) ?? "нет данных"}</span>
            <span>Максимум за период: {maximum}</span>
          </div>
        );
      })}
    </div>
  );
}

export function PresenceCharts({
  project,
  catalog,
  cards,
  plan,
  workStates,
  asOf,
  preferredSourceId,
  preferredWorkId,
}: {
  project: Project;
  catalog: Catalog;
  cards: Monitor["cards"];
  plan: Plan | null;
  workStates: Record<string, string>;
  asOf: number;
  preferredSourceId?: string;
  preferredWorkId?: string;
}) {
  const [summaryPortalReady, setSummaryPortalReady] = useState(false);
  useEffect(() => setSummaryPortalReady(true), []);
  const [sourceId, setSourceId] = useState(""),
    [workId, setWorkId] = useState(""),
    [windowChoice, setWindowChoice] = useState("all"),
    [customWindowHours, setCustomWindowHours] = useState(3),
    [chartView, setChartView] = useState<"summary" | "details">("details"),
    [activityView, setActivityView] = useState<"summary" | "details">(
      "details",
    ),
    [additionalView, setAdditionalView] = useState<"summary" | "details">(
      "details",
    ),
    [windowEnd, setWindowEnd] = useState<number | null>(null),
    [hidden, setHidden] = useState<string[]>([]);
  const [series, setSeries] = useState<SeriesPage | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true),
    [loadedPages, setLoadedPages] = useState(0),
    [retryToken, setRetryToken] = useState(0);
  const [stepOverride, setStepOverride] = useState<{
    context: string;
    value: number;
  } | null>(null);
  const [framePoint, setFramePoint] = useState<Point | null>(null);
  const [frameObservation, setFrameObservation] = useState<Observation | null>(
    null,
  );
  const appliedPreference = useRef<string | null>(null);
  useEffect(() => {
    if (!framePoint) return;
    const controller = new AbortController();
    setFrameObservation(null);
    api<Observation>(`/observations/${framePoint.id}`, {
      signal: controller.signal,
    })
      .then(setFrameObservation)
      .catch(() => {
        if (!controller.signal.aborted) setFrameObservation(null);
      });
    return () => controller.abort();
  }, [framePoint?.id]);
  const defaultSourceCard =
    cards.find((card) =>
      (card.binding?.regions || []).some((region) =>
        region.work_ids.some((workId) => {
          const work = plan?.works.find((item) => item.id === workId);
          return (
            work && workPhase(work, workStates[work.id], asOf) === "active"
          );
        }),
      ),
    ) || cards[0];
  const sourceCard =
    cards.find((card) => card.source.id === sourceId) || defaultSourceCard;
  const source = sourceCard?.source;
  const boundWorkIds = new Set(
    (sourceCard?.binding?.regions || []).flatMap((region) => region.work_ids),
  );
  const availableWorks = (plan?.works || []).filter((work) =>
    boundWorkIds.has(work.id),
  );
  const availableWorkKey = availableWorks.map((work) => work.id).join(",");
  const defaultWorkId =
    availableWorks.find(
      (work) => workPhase(work, workStates[work.id], asOf) === "active",
    )?.id ||
    availableWorks.find(
      (work) => workPhase(work, workStates[work.id], asOf) !== "completed",
    )?.id ||
    availableWorks[0]?.id ||
    "";
  const selectedWork = availableWorks.find((work) => work.id === workId);
  const smoothingContext = `${project.id}:${source?.id || ""}:${selectedWork?.id || ""}:${source?.sample_seconds || ""}`;
  const step =
    stepOverride?.context === smoothingContext
      ? stepOverride.value
      : defaultSmoothingStep(source?.sample_seconds);
  useEffect(() => {
    if (
      !preferredSourceId ||
      !cards.some((card) => card.source.id === preferredSourceId)
    )
      return;
    const preferenceKey = `${project.id}:${preferredSourceId}`;
    if (appliedPreference.current === preferenceKey) return;
    appliedPreference.current = preferenceKey;
    setSourceId(preferredSourceId);
  }, [project.id, preferredSourceId, cards]);
  useEffect(() => {
    if (
      preferredWorkId &&
      source?.id === preferredSourceId &&
      availableWorks.some((work) => work.id === preferredWorkId)
    ) {
      setWorkId(preferredWorkId);
    }
  }, [preferredWorkId, preferredSourceId, source?.id, availableWorkKey]);
  useEffect(() => {
    setWorkId((current) =>
      availableWorks.some((work) => work.id === current)
        ? current
        : defaultWorkId,
    );
  }, [project.id, source?.id, plan?.id, availableWorkKey, defaultWorkId]);
  useEffect(() => setHidden([]), [project.id, plan?.id, workId]);
  useEffect(() => setWindowEnd(null), [project.id, source?.id, workId]);
  const requestedWindowHours =
    windowChoice === "all"
      ? null
      : windowChoice === "custom"
        ? customWindowHours
        : Number(windowChoice);
  const windowHours =
    requestedWindowHours !== null &&
    Number.isFinite(requestedWindowHours) &&
    requestedWindowHours > 0
      ? requestedWindowHours
      : null;
  const seriesPath = (cursor?: SeriesPage) =>
    source && selectedWork
      ? `/projects/${project.id}/timeseries?source_id=${source.id}&work_id=${selectedWork.id}&limit=5000&display_seconds=${step}${cursor?.next_before ? `&before=${encodeURIComponent(cursor.next_before)}&before_sample_index=${cursor.next_before_sample_index}&before_id=${encodeURIComponent(cursor.next_before_id || "")}` : ""}`
      : null;
  const path = seriesPath();
  useEffect(() => {
    setSeries(null);
    setError("");
    setLoadedPages(0);
    setLoading(Boolean(path));
    if (!path) return;
    let active = true,
      inFlight = false;
    const controller = new AbortController();
    const load = async () => {
      if (inFlight) return;
      inFlight = true;
      if (active) setLoading(true);
      try {
        let page = await api<SeriesPage>(path, { signal: controller.signal });
        const seen = new Set<string>();
        let pages = 1;
        if (active) setLoadedPages(pages);
        while (
          active &&
          page.next_before &&
          pages < 100 &&
          !seen.has(
            `${page.next_before}|${page.next_before_sample_index}|${page.next_before_id}`,
          )
        ) {
          seen.add(
            `${page.next_before}|${page.next_before_sample_index}|${page.next_before_id}`,
          );
          const olderPath = seriesPath(page);
          if (!olderPath) break;
          const older = await api<SeriesPage>(olderPath, {
            signal: controller.signal,
          });
          page = {
            points: [...older.points, ...page.points],
            truncated: older.truncated,
            next_before: older.next_before,
            next_before_sample_index: older.next_before_sample_index,
            next_before_id: older.next_before_id,
          };
          pages += 1;
          if (active) setLoadedPages(pages);
          if (!older.points.length) break;
        }
        if (page.next_before) page.truncated = true;
        if (active) {
          setSeries(page);
          setError("");
        }
      } catch (error) {
        const requestError = error as Error;
        if (active && requestError.name !== "AbortError") {
          setError(
            requestError.message === "Failed to fetch"
              ? "Не удалось подключиться к API. Проверьте сервер приложения."
              : requestError.message,
          );
        }
      } finally {
        inFlight = false;
        if (active) setLoading(false);
      }
    };
    load();
    const timer = setInterval(load, 30000);
    return () => {
      active = false;
      controller.abort();
      clearInterval(timer);
    };
  }, [path, retryToken]);
  // A live source creates successive runs; keep earlier runs in the history.
  const runPoints = [...(series?.points || [])].sort(
    (a, b) => clock(a) - clock(b),
  );
  const earliest = runPoints.length ? clock(runPoints[0]) : 0;
  const latestClock = runPoints.length ? clock(runPoints.at(-1)!) : 0;
  const windowSeconds = windowHours ? windowHours * 3600 : null;
  const minimumEnd = windowSeconds
    ? Math.min(latestClock, earliest + windowSeconds)
    : latestClock;
  const visibleEnd = windowSeconds
    ? Math.min(latestClock, Math.max(minimumEnd, windowEnd ?? latestClock))
    : latestClock;
  const windowStart = windowSeconds ? visibleEnd - windowSeconds : null;
  const points = runPoints.filter(
    (point) =>
      windowStart === null ||
      (clock(point) >= windowStart && clock(point) <= visibleEnd),
  );
  const plannedClassIds = new Set(
    Object.entries(selectedWork?.resources || {})
      .filter(([, count]) => count > 0)
      .map(([id]) => Number(id)),
  );
  const lines: Line[] = catalog.classes
    .filter((c) => plannedClassIds.has(c.id))
    .map((c) => ({
      id: String(c.id),
      name: c.name,
      color: colors[c.id % colors.length],
      values: points.map((p) => p.counts[String(c.id)] ?? null),
    }));
  const additionalLines: Line[] = catalog.classes
    .filter((item) => !plannedClassIds.has(item.id))
    .map((item) => ({
      id: String(item.id),
      name: item.name,
      color: colors[item.id % colors.length],
      values: points.map((point) => point.counts[String(item.id)] ?? null),
    }))
    .filter((line) => line.values.some((value) => value !== null && value > 0));
  const additionalMeasurementsKnown = points.some((point) =>
    catalog.classes.some(
      (item) =>
        !plannedClassIds.has(item.id) &&
        point.counts[String(item.id)] !== null &&
        point.counts[String(item.id)] !== undefined,
    ),
  );
  const additionalRows: EquipmentTimelineRow[] = additionalLines.map(
    (line) => ({
      ...line,
      planned: 0,
    }),
  );
  const timelineRows: EquipmentTimelineRow[] = lines.map((line) => ({
    ...line,
    planned: selectedWork?.resources[line.id] || 0,
    activity: points.map((point, index) => {
      const count = line.values[index];
      if (count === null) return null;
      const measured = point.display_activity_by_class?.[line.id];
      const working = Math.min(count, measured?.working ?? 0);
      const idle = Math.min(count - working, measured?.idle ?? 0);
      return {
        working,
        idle,
        unknown: count - working - idle,
        continuedWorking: Math.min(
          working,
          point.continued_working_by_class?.[line.id] ?? 0,
        ),
      };
    }),
  }));
  const activities: Line[] = [
    { id: "working", name: "Работает", color: "#23846b" },
    { id: "idle", name: "Простаивает", color: "#c94653" },
  ].map((activity) => ({
    ...activity,
    values: points.map(
      (point) =>
        point.display_activity?.[activity.id as "working" | "idle"] ?? null,
    ),
  }));
  const latest = points.at(-1);
  const required = Object.values(selectedWork?.resources || {}).reduce(
    (total, count) => total + count,
    0,
  );
  const observed = lines.reduce(
    (total, line) => total + (latest?.counts[line.id] || 0),
    0,
  );
  const covered = lines.reduce(
    (total, line) =>
      total +
      Math.min(
        latest?.counts[line.id] || 0,
        selectedWork?.resources[line.id] || 0,
      ),
    0,
  );
  const excess = lines.reduce(
    (total, line) =>
      total +
      Math.max(
        0,
        (latest?.counts[line.id] || 0) -
          (selectedWork?.resources[line.id] || 0),
      ),
    0,
  );
  const excessTypes = lines.filter(
    (line) =>
      (latest?.counts[line.id] || 0) > (selectedWork?.resources[line.id] || 0),
  ).length;
  const working = latest?.display_activity?.working ?? null;
  const idle = latest?.display_activity?.idle ?? null;
  const shortfalls = lines.filter(
    (line) =>
      latest?.counts[line.id] != null &&
      latest.counts[line.id]! < (selectedWork?.resources[line.id] || 0),
  );
  const sampledLatest = lines.some(
    (line) => latest?.display_basis?.[line.id] === "sampled",
  );
  const allKnownLatest =
    !!latest &&
    lines.length > 0 &&
    lines.every((line) => latest.counts[line.id] != null);
  const hasPresenceValues = lines.some((line) =>
    line.values.some((value) => value !== null),
  );
  const hasActivityValues = timelineRows.some((row) =>
    row.values.some((value) => value !== null),
  );
  const chartStatus =
    loading && (!series || !points.length)
      ? `Загружаем историю${loadedPages ? ` · загружено страниц: ${loadedPages}` : ""}…`
      : error && !series
        ? "Не удалось загрузить данные"
        : series?.points.length && !points.length
          ? "В выбранном периоде нет измерений"
          : "Нет данных";
  return (
    <>
      <div className="analytics-dashboard" id="project-statistics">
        <section className="panel presence-panel">
          <div className="panel-heading">
            <div>
              <h2>Динамика присутствия</h2>
              <span>
                {selectedWork
                  ? `${selectedWork.code} · ${selectedWork.title}`
                  : "Выберите привязанный этап"}
              </span>
            </div>
            <InfoButton title="Динамика присутствия">
              <p>
                Каждая строка относится к одному виду техники. Пунктир
                показывает потребность этапа; красный цвет означает меньше
                плана, зелёный — по плану, жёлтый — выше плана. ID треков не
                складываются. Краткие пропуски сглаживаются, разрывы не
                соединяются.
              </p>
              <p>
                Шаг сглаживания выбирает последний устойчивый счёт в интервале.
                На графике он занимает время до следующего измерения в том же
                непрерывном прогоне; сводка по кадрам не считает этот интервал
                подтверждённой длительностью.
              </p>
              <p>
                {points.some((p) => !p.time)
                  ? "Шкала отсчитывается от начала записи."
                  : "Время указано по Москве."}
                {points.some((p) => p.demo)
                  ? " Источник использует демонстрационное повторение."
                  : ""}
              </p>
            </InfoButton>
            <div className="chart-controls">
              <label>
                Камера / видео
                <select
                  aria-label="Источник графика"
                  value={source?.id || ""}
                  onChange={(e) => {
                    setSourceId(e.target.value);
                    setFramePoint(null);
                    setWorkId("");
                    setWindowEnd(null);
                  }}
                >
                  {!cards.length && <option value="">Нет источников</option>}
                  {cards.map((c) => (
                    <option key={c.source.id} value={c.source.id}>
                      {c.source.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Этап плана
                <select
                  aria-label="Этап для графика"
                  value={selectedWork?.id || ""}
                  disabled={!availableWorks.length}
                  onChange={(event) => setWorkId(event.target.value)}
                >
                  {!availableWorks.length && (
                    <option value="">Нет привязанных этапов</option>
                  )}
                  {availableWorks.map((work) => (
                    <option key={work.id} value={work.id}>
                      {work.code} · {work.title}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Шаг сглаживания
                <select
                  aria-label="Шаг сглаживания измерений"
                  value={step}
                  onChange={(e) =>
                    setStepOverride({
                      context: smoothingContext,
                      value: Number(e.target.value),
                    })
                  }
                >
                  {SMOOTHING_STEPS.map(([value, label]) => (
                    <option value={value} key={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Длительность наблюдений
                <select
                  aria-label="Длительность отображения графиков"
                  value={windowChoice}
                  onChange={(event) => {
                    setWindowChoice(event.target.value);
                    setWindowEnd(null);
                  }}
                >
                  <option value="all">Вся история этапа</option>
                  <option value="1">1 час</option>
                  <option value="3">3 часа</option>
                  <option value="6">6 часов</option>
                  <option value="12">12 часов</option>
                  <option value="24">24 часа</option>
                  <option value="72">3 суток</option>
                  <option value="168">7 суток</option>
                  <option value="custom">Другое значение…</option>
                </select>
              </label>
              {windowChoice === "custom" && (
                <label>
                  Часов
                  <input
                    className="chart-window-custom"
                    type="number"
                    min="0.0002777778"
                    step="any"
                    required
                    value={customWindowHours}
                    onChange={(event) =>
                      setCustomWindowHours(Number(event.target.value))
                    }
                    aria-label="Произвольная длительность наблюдений в часах"
                  />
                </label>
              )}
            </div>
          </div>
          {error && (
            <p role="alert" className="banner danger">
              {error}
              <button
                type="button"
                className="button secondary chart-retry"
                onClick={() => setRetryToken((current) => current + 1)}
              >
                Повторить
              </button>
            </p>
          )}
          <div
            className="chart-view-switch"
            aria-label="Вид графика присутствия"
          >
            <button
              type="button"
              className={chartView === "summary" ? "active" : ""}
              onClick={() => setChartView("summary")}
            >
              Сводка по времени
            </button>
            <button
              type="button"
              className={chartView === "details" ? "active" : ""}
              onClick={() => setChartView("details")}
            >
              Подробная динамика
            </button>
          </div>
          {loading && series && (
            <p className="chart-loading" role="status">
              Обновляем историю…
            </p>
          )}
          {loading && !series ? (
            <div className="chart-no-data" role="status">
              {chartStatus}
            </div>
          ) : !points.length ? (
            <div className="chart-no-data">{chartStatus}</div>
          ) : !lines.length ? (
            <div className="chart-no-data">
              В текущем плане не задана потребность в технике.
            </div>
          ) : !hasPresenceValues ? (
            <div className="chart-no-data">
              Измерения получены, но устойчивый счёт пока не подтверждён.
            </div>
          ) : chartView === "summary" ? (
            <AbsenceHistogram
              points={points}
              lines={lines.filter((line) => !hidden.includes(line.id))}
            />
          ) : (
            <EquipmentTimeline
              points={points}
              rows={timelineRows.filter((row) => !hidden.includes(row.id))}
              displayStep={step}
              mode="presence"
              onPointClick={setFramePoint}
            />
          )}
          <div className="chart-history-controls" aria-label="История графиков">
            <span>
              {points.length
                ? `${timeLabel(points[0])} - ${timeLabel(points.at(-1)!)}`
                : chartStatus}
            </span>
            <button
              type="button"
              className="button secondary"
              disabled={!windowSeconds || visibleEnd <= minimumEnd}
              onClick={() =>
                setWindowEnd(
                  Math.max(minimumEnd, visibleEnd - windowSeconds! / 2),
                )
              }
            >
              ← Ранее
            </button>
            <button
              type="button"
              className="button secondary"
              disabled={!windowSeconds || visibleEnd >= latestClock}
              onClick={() =>
                setWindowEnd(
                  Math.min(latestClock, visibleEnd + windowSeconds! / 2),
                )
              }
            >
              Новее →
            </button>
            <button
              type="button"
              className="button secondary"
              disabled={windowEnd === null}
              onClick={() => setWindowEnd(null)}
            >
              К live
            </button>
          </div>
          {windowSeconds && latestClock > minimumEnd && (
            <input
              className="chart-timeline-scroll"
              type="range"
              min={0}
              max={1000}
              step={1}
              value={Math.round(
                ((visibleEnd - minimumEnd) / (latestClock - minimumEnd)) * 1000,
              )}
              onChange={(event) =>
                setWindowEnd(
                  minimumEnd +
                    (Number(event.target.value) / 1000) *
                      (latestClock - minimumEnd),
                )
              }
              aria-label="Прокрутка графика по времени"
            />
          )}
          <div className="chart-legend">
            {lines.map((l) => (
              <label key={l.id}>
                <input
                  type="checkbox"
                  checked={!hidden.includes(l.id)}
                  onChange={(e) =>
                    setHidden((v) =>
                      e.target.checked
                        ? v.filter((id) => id !== l.id)
                        : [...v, l.id],
                    )
                  }
                />
                <i style={{ background: l.color }} />
                {l.name}
              </label>
            ))}
          </div>
          {series?.truncated && (
            <p className="chart-limit-note" role="status">
              Достигнут предел истории: показаны последние 500 000 измерений.
            </p>
          )}
        </section>
        {summaryPortalReady && document.getElementById("overview-composition")
          ? createPortal(
              <Composition
                lines={lines}
                point={latest}
                planned={selectedWork?.resources || {}}
              />,
              document.getElementById("overview-composition")!,
            )
          : null}
        <section className="panel activity-chart">
          <div className="panel-help-heading">
            <h2>Активность техники</h2>
            <InfoButton title="Активность техники">
              <p>
                Каждая строка относится к одному виду техники. Зелёная часть
                показывает подтверждённую работу, красная — подтверждённый
                простой, серая — неопределённую активность. Пунктир — план. Для
                простоя требуется непрерывный пригодный обзор.
              </p>
              <p>
                Сводка показывает машино-время: число машин умножается на
                длительность непрерывно наблюдаемого интервала. Разрывы и
                отдельные кадры не добавляются к работе или простою.
              </p>
            </InfoButton>
          </div>
          <div
            className="chart-view-switch"
            aria-label="Вид активности техники"
          >
            <button
              type="button"
              className={activityView === "summary" ? "active" : ""}
              onClick={() => setActivityView("summary")}
            >
              Сводка по времени
            </button>
            <button
              type="button"
              className={activityView === "details" ? "active" : ""}
              onClick={() => setActivityView("details")}
            >
              Подробная динамика
            </button>
          </div>
          {loading && !series ? (
            <div className="chart-no-data" role="status">
              {chartStatus}
            </div>
          ) : !points.length ? (
            <div className="chart-no-data">{chartStatus}</div>
          ) : !hasActivityValues ? (
            <div className="chart-no-data">
              Измерения получены, но активность пока не определена.
            </div>
          ) : activityView === "summary" ? (
            <ActivitySummary
              points={points}
              classes={timelineRows.filter((row) => !hidden.includes(row.id))}
            />
          ) : (
            <EquipmentTimeline
              points={points}
              rows={timelineRows.filter((row) => !hidden.includes(row.id))}
              displayStep={step}
              mode="activity"
              onPointClick={setFramePoint}
            />
          )}
        </section>
        <section className="panel result-chart">
          <div className="panel-help-heading">
            <h2>Присутствие дополнительной техники</h2>
            <InfoButton title="Присутствие дополнительной техники">
              <p>
                Показаны обнаруженные виды техники, для которых в выбранном
                этапе не задано плановое количество. График использует тот же
                источник, этап, окно и шаг от 30 секунд, что и динамика
                присутствия. Разрывы между запусками анализа не соединяются.
              </p>
            </InfoButton>
          </div>
          <div
            className="chart-view-switch"
            aria-label="Вид графика дополнительной техники"
          >
            <button
              type="button"
              className={additionalView === "summary" ? "active" : ""}
              onClick={() => setAdditionalView("summary")}
            >
              Сводка по времени
            </button>
            <button
              type="button"
              className={additionalView === "details" ? "active" : ""}
              onClick={() => setAdditionalView("details")}
            >
              Подробная динамика
            </button>
          </div>
          {loading && !series ? (
            <div className="chart-no-data" role="status">
              {chartStatus}
            </div>
          ) : !points.length ? (
            <div className="chart-no-data">{chartStatus}</div>
          ) : !additionalRows.length ? (
            <div className="chart-no-data">
              {additionalMeasurementsKnown
                ? "Дополнительная техника в выбранном периоде не обнаружена."
                : "Недостаточно данных для оценки дополнительной техники."}
            </div>
          ) : additionalView === "summary" ? (
            <AdditionalSummary rows={additionalRows} />
          ) : (
            <EquipmentTimeline
              points={points}
              rows={additionalRows}
              displayStep={step}
              mode="additional"
              onPointClick={setFramePoint}
            />
          )}
        </section>
        {summaryPortalReady && document.getElementById("overview-stage-summary")
          ? createPortal(
              <section
                className="panel analytics-insights"
                data-tone={
                  shortfalls.length || excess > 0 || (idle ?? 0) > 0
                    ? "warning"
                    : !latest ||
                        sampledLatest ||
                        !allKnownLatest ||
                        working === null ||
                        idle === null
                      ? "unknown"
                      : "ok"
                }
              >
                <div className="panel-help-heading">
                  <div className="analytics-insights-head">
                    <h2>Итог по этапу</h2>
                    <p>
                      {selectedWork
                        ? `${selectedWork.code} · ${selectedWork.title}`
                        : "Выберите этап плана"}
                    </p>
                  </div>
                  <InfoButton title="Итог по этапу">
                    <p>
                      Сравнение последнего измерения с потребностью выбранного
                      этапа. Работа и простой показаны только при подтверждённой
                      активности.
                    </p>
                  </InfoButton>
                </div>
                <div className="analytics-insights-metrics">
                  <div>
                    <strong>{allKnownLatest ? covered : "—"}</strong>
                    <span>наблюдается из {required} по плану</span>
                    {allKnownLatest && excess > 0 && (
                      <small>
                        Сверх плана: {excess} ед. ({excessTypes} видов техники)
                      </small>
                    )}
                  </div>
                  <div>
                    <strong>{working ?? "-"}</strong>
                    <span>работает</span>
                  </div>
                  <div>
                    <strong>{idle ?? "-"}</strong>
                    <span>простаивает</span>
                  </div>
                </div>
                {!sampledLatest && (
                  <div className="analytics-insights-conclusion">
                    {shortfalls.length ? (
                      <>
                        <strong>Требует внимания</strong>
                        <p>
                          {shortfalls
                            .map(
                              (line) =>
                                `${line.name}: ${latest?.counts[line.id]} из ${selectedWork?.resources[line.id]}`,
                            )
                            .join("; ")}
                          .
                        </p>
                      </>
                    ) : (idle ?? 0) > 0 ? (
                      <>
                        <strong>Есть простой техники</strong>
                        <p>
                          Наблюдается {observed} машин; из них {idle}{" "}
                          простаивает.
                        </p>
                      </>
                    ) : allKnownLatest && covered >= required && !excess ? (
                      <>
                        <strong>Состав соответствует плану</strong>
                        <p>
                          Наблюдается {observed} машин. В работе{" "}
                          {working ?? "нет данных"}, в простое{" "}
                          {idle ?? "нет данных"}.
                        </p>
                      </>
                    ) : (
                      <>
                        <strong>Ожидаем подтверждённые данные</strong>
                        <p>
                          Вывод появится после устойчивого наблюдения выбранного
                          этапа.
                        </p>
                      </>
                    )}
                  </div>
                )}
              </section>,
              document.getElementById("overview-stage-summary")!,
            )
          : null}
      </div>
      {framePoint && sourceCard && (
        <CameraFrameViewer
          card={{
            ...sourceCard,
            observation:
              frameObservation?.id === framePoint.id
                ? frameObservation
                : {
                    id: framePoint.id,
                    captured_at: framePoint.time,
                    offset_seconds: framePoint.offset_seconds,
                    detections: [],
                    quality: { usable: true },
                    model: { name: "" },
                  },
          }}
          catalog={catalog}
          onClose={() => setFramePoint(null)}
        />
      )}
    </>
  );
}
