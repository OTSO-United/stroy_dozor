import { useEffect, useRef, useState } from "react";
import { Truck } from "lucide-react";
import { duration, type Point } from "./api";
import { equipmentIconUrl } from "./equipmentIcons";

export type EquipmentTimelineRow = {
  id: string;
  name: string;
  color: string;
  planned: number;
  values: (number | null)[];
  activity?: ({
    working: number;
    idle: number;
    unknown: number;
    continuedWorking: number;
  } | null)[];
};

const clock = (point: Point) =>
  point.time ? Date.parse(point.time) / 1000 : point.offset_seconds;
const labelTime = (point: Point) =>
  point.time
    ? new Intl.DateTimeFormat("ru-RU", {
        timeZone: "Europe/Moscow",
        day: "2-digit",
        month: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      }).format(new Date(point.time))
    : duration(point.offset_seconds);
const axisTime = (first: Point, seconds: number) =>
  first.time
    ? new Intl.DateTimeFormat("ru-RU", {
        timeZone: "Europe/Moscow",
        day: "2-digit",
        month: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      }).format(new Date(seconds * 1000))
    : duration(seconds);
function nearest(points: Point[], target: number) {
  let low = 0,
    high = points.length - 1;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (clock(points[middle]) < target) low = middle + 1;
    else high = middle;
  }
  return low > 0 &&
    Math.abs(clock(points[low - 1]) - target) <
      Math.abs(clock(points[low]) - target)
    ? low - 1
    : low;
}

export function EquipmentTimeline({
  points,
  rows,
  displayStep,
  mode,
  onPointClick,
}: {
  points: Point[];
  rows: EquipmentTimelineRow[];
  displayStep: number;
  mode: "presence" | "activity" | "additional";
  onPointClick?: (point: Point) => void;
}) {
  const viewport = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(980);
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => {
    if (!viewport.current) return;
    const observer = new ResizeObserver(([entry]) =>
      setWidth(Math.max(900, entry.contentRect.width)),
    );
    observer.observe(viewport.current);
    return () => observer.disconnect();
  }, []);
  if (!points.length || !rows.length) return null;
  const plotLeft = 220,
    plotRight = width - 28;
  const plotWidth = plotRight - plotLeft;
  const first = clock(points[0]),
    last = clock(points.at(-1)!);
  const intervalEnd =
    last +
    (points.length > 1
      ? Math.max(displayStep, points.at(-1)!.sample_seconds || 0)
      : 0);
  const span = Math.max(1, intervalEnd - first);
  const x = (index: number) =>
    points.length === 1
      ? plotLeft + plotWidth / 2
      : plotLeft + ((clock(points[index]) - first) / span) * plotWidth;
  const sameSeries = (row: EquipmentTimelineRow, left: number, right: number) =>
    left >= 0 &&
    right < points.length &&
    points[left].run_id === points[right].run_id &&
    points[left].segment === points[right].segment &&
    clock(points[right]) > clock(points[left]) &&
    clock(points[right]) - clock(points[left]) <=
      Math.max(displayStep * 1.5, 90) &&
    row.values[left] !== null &&
    row.values[right] !== null;
  const linked = sameSeries;
  const rowHeight = 100,
    topPadding = 10;
  const axisTop = topPadding + rows.length * rowHeight + 12;
  const height = axisTop + 45;
  const legend =
    mode === "presence"
      ? [
          ["below", "Ниже плана"],
          ["match", "По плану"],
          ["above", "Выше плана"],
          ["planned", "Плановое количество"],
        ]
      : mode === "additional"
        ? [["above", "Обнаружена вне плана"]]
        : [
            ["working", "Работает"],
            ["idle", "Простаивает"],
            ["unknown", "Активность не определена"],
            ["planned", "Плановое количество"],
          ];
  const focused = hover === null ? null : points[hover];
  return (
    <div className="equipment-timeline">
      <div className="equipment-timeline-legend">
        {legend.map(([kind, title]) => (
          <span className={kind} key={kind}>
            <i />
            {title}
          </span>
        ))}
      </div>
      <div className="equipment-timeline-viewport" ref={viewport}>
        <svg
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={
            mode === "presence"
              ? "Количество техники по каждому виду и плановое значение во времени"
              : mode === "additional"
                ? "Присутствие видов техники, не предусмотренных планом этапа"
                : "Работа, простой и неопределённая активность по каждому виду техники во времени"
          }
        >
          <title>
            Разрывы означают отсутствие непрерывных данных. Пунктир показывает
            план.
          </title>
          {rows.map((row, rowIndex) => {
            const top = topPadding + rowIndex * rowHeight;
            const baseline = top + 81;
            const maximum = row.values.reduce<number>(
              (largest, value) => Math.max(largest, value ?? 0),
              Math.max(1, row.planned),
            );
            const scale = 49 / maximum;
            const y = (value: number) => baseline - value * scale;
            const iconUrl = equipmentIconUrl(row.id);
            const valueLabels = new Map<number, number>();
            if (mode !== "activity") {
              let lastLabelRight = -Infinity;
              for (let start = 0; start < points.length; ) {
                const value = row.values[start];
                if (value === null) {
                  start += 1;
                  continue;
                }
                let end = start;
                while (
                  end + 1 < points.length &&
                  row.values[end + 1] === value &&
                  sameSeries(row, end, end + 1)
                )
                  end += 1;
                const adjacentSpacing = [
                  start > 0 ? x(start) - x(start - 1) : Infinity,
                  end + 1 < points.length ? x(end + 1) - x(end) : Infinity,
                ];
                const sampleWidth = Math.min(...adjacentSpacing);
                const availableWidth =
                  x(end) -
                  x(start) +
                  (Number.isFinite(sampleWidth) ? sampleWidth : plotWidth);
                const labelWidth = Math.max(26, String(value).length * 9 + 12);
                const labelX = Math.max(
                  plotLeft + labelWidth / 2 + 2,
                  Math.min(
                    plotRight - labelWidth / 2 - 2,
                    (x(start) + x(end)) / 2,
                  ),
                );
                if (
                  availableWidth >= labelWidth &&
                  labelX - labelWidth / 2 >= lastLabelRight + 5
                ) {
                  valueLabels.set(start, labelX);
                  lastLabelRight = labelX + labelWidth / 2;
                }
                start = end + 1;
              }
            }
            return (
              <g key={row.id}>
                <rect
                  x="0"
                  y={top}
                  width={width}
                  height={rowHeight - 4}
                  rx="8"
                  fill={rowIndex % 2 ? "#f6f9f7" : "#fbfcfb"}
                />
                {iconUrl ? (
                  <image
                    href={iconUrl}
                    x={4}
                    y={top + 21}
                    width={44}
                    height={44}
                    preserveAspectRatio="xMidYMid meet"
                    aria-hidden="true"
                  />
                ) : (
                  <Truck
                    x={14}
                    y={top + 27}
                    width={29}
                    height={29}
                    color={row.color}
                    strokeWidth={2.3}
                    aria-hidden="true"
                  />
                )}
                <text className="equipment-timeline-name" x="52" y={top + 44}>
                  {row.name}
                </text>
                <text
                  className="equipment-timeline-plan-label"
                  x="52"
                  y={top + 66}
                >
                  {mode === "additional"
                    ? "В плане отсутствует"
                    : `План: ${row.planned} ед.`}
                </text>
                {Array.from({ length: maximum + 1 }, (_, value) => (
                  <text
                    key={`y-${value}`}
                    className="equipment-timeline-y-label"
                    x={plotLeft - 12}
                    y={y(value) + 4}
                    textAnchor="end"
                  >
                    {value}
                  </text>
                ))}
                {[0, 1, 2, 3, 4, 5].map((tick) => (
                  <line
                    key={tick}
                    className="equipment-timeline-grid"
                    x1={plotLeft + (plotWidth * tick) / 5}
                    x2={plotLeft + (plotWidth * tick) / 5}
                    y1={top + 19}
                    y2={baseline + 4}
                  />
                ))}
                <line
                  className="equipment-timeline-baseline"
                  x1={plotLeft}
                  x2={plotRight}
                  y1={baseline}
                  y2={baseline}
                />
                {points.map((point, index) => {
                  const value = row.values[index];
                  if (value === null) return null;
                  const after = linked(row, index, index + 1);
                  const left = x(index);
                  // A sample represents its smoothing interval until the next
                  // measurement in the same continuous run.
                  const fallbackWidth = Math.max(
                    4,
                    (Math.max(displayStep, point.sample_seconds || 0) / span) *
                      plotWidth,
                  );
                  const nextX =
                    index + 1 < points.length ? x(index + 1) : plotRight;
                  const right = after
                    ? nextX
                    : Math.min(plotRight, nextX, left + fallbackWidth);
                  const longEnough = right - left >= 2;
                  const status =
                    value < row.planned
                      ? "below"
                      : value > row.planned
                        ? "above"
                        : "match";
                  const activity = row.activity?.[index];
                  const working = Math.min(value, activity?.working ?? 0);
                  const idle = Math.min(value - working, activity?.idle ?? 0);
                  const unknown = Math.max(0, value - working - idle);
                  if (mode === "activity") {
                    const blocks = [
                      { kind: "working", count: working, lower: 0 },
                      { kind: "idle", count: idle, lower: working },
                      {
                        kind: "unknown",
                        count: unknown,
                        lower: working + idle,
                      },
                    ];
                    return (
                      <g key={point.id}>
                        {blocks
                          .filter((block) => block.count > 0)
                          .map((block) => (
                            <g key={block.kind}>
                              {longEnough ? (
                                <rect
                                  className={`equipment-timeline-fill ${block.kind}`}
                                  x={left}
                                  y={y(block.lower + block.count)}
                                  width={right - left}
                                  height={block.count * scale}
                                />
                              ) : (
                                <line
                                  className={`equipment-timeline-stroke ${block.kind}`}
                                  x1={x(index)}
                                  x2={x(index)}
                                  y1={y(block.lower + block.count)}
                                  y2={y(block.lower)}
                                  strokeWidth="4"
                                />
                              )}
                            </g>
                          ))}
                      </g>
                    );
                  }
                  return (
                    <g key={point.id}>
                      {longEnough ? (
                        <>
                          {value > 0 && (
                            <rect
                              className={`equipment-timeline-fill ${status}`}
                              x={left}
                              y={y(value)}
                              width={right - left}
                              height={baseline - y(value)}
                            />
                          )}
                          <line
                            className={`equipment-timeline-stroke ${status}`}
                            x1={left}
                            x2={right}
                            y1={y(value)}
                            y2={y(value)}
                          />
                        </>
                      ) : (
                        <line
                          className={"equipment-timeline-stroke " + status}
                          x1={left}
                          x2={right}
                          y1={y(value)}
                          y2={y(value)}
                        />
                      )}
                      <circle
                        className={"equipment-timeline-dot " + status}
                        cx={x(index)}
                        cy={y(value)}
                        r="2"
                      />
                      {valueLabels.has(index) && (
                        <text
                          className={`equipment-timeline-value ${status}`}
                          x={valueLabels.get(index)}
                          y={Math.max(top + 26, y(value) - 7)}
                          textAnchor="middle"
                        >
                          {value}
                        </text>
                      )}
                    </g>
                  );
                })}
                {mode !== "additional" && (
                  <line
                    className="equipment-timeline-planned"
                    x1={plotLeft}
                    x2={plotRight}
                    y1={y(row.planned)}
                    y2={y(row.planned)}
                  />
                )}
                <rect
                  x={plotLeft}
                  y={top + 15}
                  width={plotWidth}
                  height="72"
                  fill="transparent"
                  onMouseMove={(event) => {
                    const bounds = event.currentTarget.getBoundingClientRect();
                    setHover(
                      nearest(
                        points,
                        first +
                          ((event.clientX - bounds.left) / bounds.width) * span,
                      ),
                    );
                  }}
                  onMouseLeave={() => setHover(null)}
                  onClick={(event) => {
                    if (!onPointClick) return;
                    const bounds = event.currentTarget.getBoundingClientRect();
                    const index = nearest(
                      points,
                      first +
                        ((event.clientX - bounds.left) / bounds.width) * span,
                    );
                    onPointClick(points[index]);
                  }}
                  style={{ cursor: onPointClick ? "pointer" : "default" }}
                  aria-label={
                    onPointClick ? "Открыть кадр измерения" : undefined
                  }
                />
              </g>
            );
          })}
          {hover !== null && (
            <line
              className="equipment-timeline-cursor"
              x1={x(hover)}
              x2={x(hover)}
              y1={topPadding + 18}
              y2={axisTop - 16}
            />
          )}
          {Array.from({ length: points.length === 1 ? 1 : 6 }, (_, tick) => {
            const fraction = points.length === 1 ? 0.5 : tick / 5;
            return (
              <text
                key={tick}
                className="equipment-timeline-axis-label"
                x={plotLeft + fraction * plotWidth}
                y={axisTop + 19}
                textAnchor={
                  tick === 0 && points.length > 1
                    ? "start"
                    : tick === 5
                      ? "end"
                      : "middle"
                }
              >
                {axisTime(points[0], first + fraction * span)}
              </text>
            );
          })}
          <text
            className="equipment-timeline-axis-title"
            x={(plotLeft + plotRight) / 2}
            y={axisTop + 40}
            textAnchor="middle"
          >
            Время
          </text>
        </svg>
      </div>
      {focused && (
        <div className="equipment-timeline-readout" role="status">
          <strong>{labelTime(focused)}</strong>
          {onPointClick && <small>Нажмите на график, чтобы открыть кадр</small>}
          {rows.map((row) => {
            const value = row.values[hover!];
            const raw = focused.raw_counts?.[row.id];
            const activity = row.activity?.[hover!];
            return (
              <span key={row.id}>
                <i style={{ background: row.color }} />
                {row.name}: {value ?? "нет данных"}
                {mode === "additional"
                  ? " · вне плана"
                  : ` / план ${row.planned}`}
                {mode === "activity" && value !== null
                  ? ` · работает ${activity?.working ?? 0}, простой ${activity?.idle ?? 0}, не определено ${activity?.unknown ?? value}${activity?.continuedWorking ? ` · из работающих ${activity.continuedWorking} — продолжение последнего наблюдения` : ""}`
                  : ""}
                {mode === "presence" &&
                raw !== null &&
                raw !== undefined &&
                raw !== value
                  ? ` · на исходном кадре ${raw}`
                  : ""}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}
