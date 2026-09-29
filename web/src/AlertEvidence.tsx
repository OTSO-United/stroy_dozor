import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Maximize2, Minimize2, RotateCcw, ZoomIn, ZoomOut } from "lucide-react";
import {
  api,
  date,
  duration,
  type Alert,
  type Catalog,
  type Observation,
  type Source,
} from "./api";
import { equipmentColor } from "./PresenceCharts";
import { FrameDetections } from "./FrameDetections";
import { bindFrameWheel } from "./frameInteraction";
import "./detector.css";

export function alertRows(alert: Alert, catalog: Catalog) {
  const findingCountByClass = Object.fromEntries(
    (alert.details.classes || []).map((item) => [item.class_id, item.count]),
  );
  const relevant = { ...(alert.details.expected || {}) };
  for (const item of alert.details.classes || []) {
    if (!(item.class_id in relevant)) relevant[item.class_id] = 0;
  }
  return Object.entries(relevant)
    .map(([id, expected]) => {
      const found = alert.details.counts?.[id] ?? null;
      return {
        id,
        name:
          catalog.classes.find((c) => String(c.id) === id)?.name ||
          `Класс ${id}`,
        expected,
        found,
        missing: found === null ? null : Math.max(0, expected - found),
        excess:
          alert.kind === "excess"
            ? findingCountByClass[id] || 0
            : found === null
              ? null
              : Math.max(0, found - expected),
        idle: findingCountByClass[id] || 0,
      };
    })
    .filter((row) =>
      alert.kind === "missing"
        ? (row.missing ?? 0) > 0
        : alert.kind === "idle"
          ? row.idle > 0
          : (row.excess ?? 0) > 0,
    );
}
export function AlertBreakdown({
  alert,
  catalog,
  compact = false,
}: {
  alert: Alert;
  catalog: Catalog;
  compact?: boolean;
}) {
  const rows = alertRows(alert, catalog);
  if (compact)
    return (
      <span className="alert-breakdown">
        {rows
          .filter(
            (r) =>
              (alert.kind === "missing"
                ? r.missing
                : alert.kind === "idle"
                  ? r.idle
                  : r.excess) !== 0,
          )
          .map((r) => (
            <span key={r.id}>
              {r.name}:{" "}
              {alert.kind === "idle"
                ? `простаивает ${r.idle}`
                : `на кадре ${r.found ?? "нет данных"} / план ${r.expected}`}
              {r.found !== null &&
                alert.kind !== "idle" &&
                (alert.kind === "missing"
                  ? ` · не хватает ${r.missing}`
                  : ` · лишних ${r.excess}`)}
            </span>
          ))}
      </span>
    );
  return (
    <div className="table-wrap">
      <table className="evidence-counts">
        <colgroup>
          <col className="evidence-counts-name" />
          <col className="evidence-counts-plan" />
          <col className="evidence-counts-found" />
          <col className="evidence-counts-difference" />
        </colgroup>
        <thead>
          <tr>
            <th>Техника</th>
            <th>План</th>
            <th>На кадре</th>
            <th>{alert.kind === "idle" ? "Простой" : "Разница"}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td>{r.name}</td>
              <td>{r.expected}</td>
              <td>{r.found ?? "-"}</td>
              <td>
                {alert.kind === "idle"
                  ? `${r.idle} простаивает`
                  : r.found === null
                    ? "Неизвестно"
                    : r.missing
                      ? `Не хватает ${r.missing}`
                      : r.excess
                        ? `Лишних ${r.excess}`
                        : "По плану"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
type Frame = Observation & {
  image_url: string;
  deviation_class_ids?: string[];
  missing_class_ids?: string[];
};
type AbsenceStat = {
  class_id: string;
  present_seconds: number;
  absent_seconds: number;
  pending_seconds: number;
  unknown_seconds: number;
  absence_ratio: number | null;
};
type Evidence = {
  source: Source;
  frames: Frame[];
  basis: Frame | null;
  keyframes: Frame[];
  deviation_class_ids: string[];
  missing_class_ids: string[];
  absence_stats: AbsenceStat[];
  stats_truncated: boolean;
  next_offset: number | null;
  video_url: string | null;
};
type Detection = Frame["detections"][number];
const exactFrameTime = (value: string | null) =>
  value
    ? new Intl.DateTimeFormat("ru-RU", {
        timeZone: "Europe/Moscow",
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      }).format(new Date(value))
    : "Время не задано";

export function AlertEvidence({
  alert,
  catalog,
}: {
  alert: Alert;
  catalog: Catalog;
}) {
  const [data, setData] = useState<Evidence | null>(null),
    [frames, setFrames] = useState<Frame[]>([]),
    [selected, setSelected] = useState<Frame | null>(null);
  const [offset, setOffset] = useState(0),
    [loading, setLoading] = useState(false),
    [error, setError] = useState(""),
    [showVideo, setShowVideo] = useState(false),
    [showFrames, setShowFrames] = useState(true),
    [showLabels, setShowLabels] = useState(true),
    [zoom, setZoom] = useState(100),
    [frameFullscreen, setFrameFullscreen] = useState(false),
    [keyframeIndex, setKeyframeIndex] = useState<number | null>(null),
    [showDetails, setShowDetails] = useState(false),
    [evidenceClassId, setEvidenceClassId] = useState("all"),
    [hiddenClassIds, setHiddenClassIds] = useState<Set<string>>(new Set());
  const frameViewer = useRef<HTMLDivElement>(null),
    frameViewport = useRef<HTMLDivElement>(null);
  const className = (id: string) =>
    catalog.classes.find((item) => String(item.id) === id)?.name ||
    `Класс ${id}`;
  const detectionGroups = useMemo(() => {
    const groups = new Map<string, Detection[]>();
    for (const detection of selected?.detections || []) {
      if (detection.observed === false) continue;
      const group = groups.get(detection.class_id) || [];
      group.push(detection);
      groups.set(detection.class_id, group);
    }
    return Array.from(groups, ([classId, detections]) => ({
      classId,
      detections,
    })).sort((left, right) =>
      className(left.classId).localeCompare(className(right.classId), "ru"),
    );
  }, [selected, catalog]);
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setError("");
    setLoading(true);
    if (!offset) setSelected(null);
    const classQuery =
      evidenceClassId === "all"
        ? ""
        : `&class_id=${encodeURIComponent(evidenceClassId)}`;
    api<Evidence>(
      `/alerts/${alert.id}/evidence?offset=${offset}${classQuery}`,
      { signal: controller.signal },
    )
      .then((value) => {
        if (!active) return;
        setData(value);
        setFrames((old) => {
          const merged = offset
            ? [...old, ...value.frames]
            : [...value.frames, ...(value.basis ? [value.basis] : [])];
          return Array.from(
            new Map(merged.map((frame) => [frame.id, frame])).values(),
          ).sort((a, b) => a.offset_seconds - b.offset_seconds);
        });
        if (!offset)
          setSelected(
            value.keyframes[0] || value.basis || value.frames[0] || null,
          );
      })
      .catch((e) => {
        if (active && e.name !== "AbortError") setError(e.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [alert.id, offset, evidenceClassId]);
  useEffect(() => {
    setEvidenceClassId("all");
    setOffset(0);
    setFrames([]);
    setKeyframeIndex(null);
    setShowDetails(false);
  }, [alert.id]);
  useEffect(() => {
    const syncFullscreen = () =>
      setFrameFullscreen(document.fullscreenElement === frameViewer.current);
    document.addEventListener("fullscreenchange", syncFullscreen);
    return () =>
      document.removeEventListener("fullscreenchange", syncFullscreen);
  }, []);
  useEffect(() => {
    const viewport = frameViewport.current;
    if (!viewport) return;
    return bindFrameWheel(viewport, zoom, changeZoom);
  }, [zoom, selected?.id, showVideo, showDetails]);

  const zoomAnchor = useRef<{
    x: number;
    y: number;
    fractionX: number;
    fractionY: number;
  } | null>(null);
  useLayoutEffect(() => {
    const viewport = frameViewport.current;
    const anchor = zoomAnchor.current;
    if (!viewport || !anchor) return;
    viewport.scrollLeft = anchor.fractionX * viewport.scrollWidth - anchor.x;
    viewport.scrollTop = anchor.fractionY * viewport.scrollHeight - anchor.y;
    zoomAnchor.current = null;
  }, [zoom]);
  useEffect(() => {
    if (keyframeIndex === null) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setKeyframeIndex(null);
      if (event.key === "ArrowLeft") {
        setKeyframeIndex((current) =>
          current === null ? null : Math.max(0, current - 1),
        );
      }
      if (event.key === "ArrowRight") {
        setKeyframeIndex((current) =>
          current === null
            ? null
            : Math.min((data?.keyframes.length || 1) - 1, current + 1),
        );
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [keyframeIndex, data?.keyframes.length]);
  function changeZoom(value: number, anchor?: { x: number; y: number }) {
    const next = Math.min(400, Math.max(100, value));
    const viewport = frameViewport.current;
    if (viewport && next !== zoom) {
      const x = anchor?.x ?? viewport.clientWidth / 2;
      const y = anchor?.y ?? viewport.clientHeight / 2;
      zoomAnchor.current = {
        x,
        y,
        fractionX:
          (viewport.scrollLeft + x) / Math.max(1, viewport.scrollWidth),
        fractionY:
          (viewport.scrollTop + y) / Math.max(1, viewport.scrollHeight),
      };
    }
    setZoom(next);
  }

  async function toggleFrameFullscreen() {
    try {
      if (document.fullscreenElement === frameViewer.current) {
        await document.exitFullscreen();
      } else {
        await frameViewer.current?.requestFullscreen();
      }
    } catch {
      setError("Браузер не разрешил открыть кадр на весь экран.");
    }
  }

  function setClassVisible(classId: string, visible: boolean) {
    setHiddenClassIds((current) => {
      const next = new Set(current);
      if (visible) next.delete(classId);
      else next.add(classId);
      return next;
    });
  }
  const keyframe =
    keyframeIndex === null ? null : data?.keyframes[keyframeIndex];
  const keyframeClasses = [
    ...new Set(keyframe?.detections.map((item) => item.class_id) || []),
  ];
  return (
    <div className="alert-evidence">
      <h3>{data?.source.name || "Свидетельства с камеры"}</h3>
      <p className="hint">
        Эпизод: {date(alert.first_seen)} - {date(alert.last_seen)}
      </p>
      {error && (
        <p role="alert" className="banner danger">
          {error}
        </p>
      )}
      {data?.deviation_class_ids.length ? (
        <section className="absence-evidence-summary">
          <div className="absence-evidence-heading">
            <div>
              <h4>
                {alert.kind === "missing"
                  ? "Ключевые кадры подтверждённого отсутствия"
                  : alert.kind === "idle"
                    ? "Кадры подтверждённого простоя"
                    : "Кадры техники сверх плана"}
              </h4>
            </div>
            {data.deviation_class_ids.length > 1 && (
              <label>
                Вид техники
                <select
                  aria-label="Вид техники"
                  title={
                    evidenceClassId === "all"
                      ? "Все виды техники"
                      : className(evidenceClassId)
                  }
                  value={evidenceClassId}
                  onChange={(event) => {
                    setFrames([]);
                    setOffset(0);
                    setEvidenceClassId(event.target.value);
                  }}
                >
                  <option value="all">Все виды техники</option>
                  {data.deviation_class_ids.map((classId) => (
                    <option key={classId} value={classId}>
                      {className(classId)}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
          {alert.kind === "missing" && (
            <div className="absence-stat-grid">
              {data.absence_stats
                .filter(
                  (stat) =>
                    evidenceClassId === "all" ||
                    stat.class_id === evidenceClassId,
                )
                .map((stat) => (
                  <button
                    type="button"
                    key={stat.class_id}
                    className={
                      evidenceClassId === stat.class_id ? "active" : ""
                    }
                    onClick={() => {
                      setFrames([]);
                      setOffset(0);
                      setEvidenceClassId(stat.class_id);
                    }}
                  >
                    <span>{className(stat.class_id)}</span>
                    <strong>
                      {stat.absence_ratio === null
                        ? "-"
                        : `${Math.round(stat.absence_ratio * 100)}%`}
                    </strong>
                    <small>
                      отсутствовала {duration(stat.absent_seconds)} из{" "}
                      {duration(stat.absent_seconds + stat.present_seconds)}
                    </small>
                  </button>
                ))}
            </div>
          )}
          {alert.kind === "missing" && data.stats_truncated && (
            <p className="hint">
              Доля рассчитана по первым 5 000 измерениям эпизода.
            </p>
          )}
          <div
            className="absence-keyframes"
            aria-label="Ключевые кадры отсутствия"
          >
            {loading ? (
              <p role="status">Загрузка свидетельств...</p>
            ) : data.keyframes.length ? (
              data.keyframes.map((frame, index) => (
                <button
                  type="button"
                  key={frame.id}
                  className={selected?.id === frame.id ? "active" : ""}
                  onClick={() => {
                    setSelected(frame);
                    setKeyframeIndex(index);
                  }}
                >
                  <img loading="lazy" src={frame.image_url} alt="" />
                  <strong>{exactFrameTime(frame.captured_at)}</strong>
                  <small>
                    {(frame.deviation_class_ids || [])
                      .filter(
                        (classId) =>
                          evidenceClassId === "all" ||
                          classId === evidenceClassId,
                      )
                      .map(className)
                      .join(", ")}
                  </small>
                </button>
              ))
            ) : (
              <p className="hint">
                Для выбранного класса нет сохранённых кадров этого отклонения.
              </p>
            )}
          </div>
        </section>
      ) : null}
      {data && (
        <button
          type="button"
          className="button secondary"
          onClick={() => setShowDetails((value) => !value)}
        >
          {showDetails ? "Скрыть подробности" : "Подробнее"}
        </button>
      )}
      {showDetails && (
        <div className="alert-evidence-details">
          {selected ? (
            <>
              {showVideo && data?.video_url ? (
                <video
                  key={selected.id}
                  controls
                  preload="metadata"
                  src={`${data.video_url}#t=${Math.max(0, selected.offset_seconds)}`}
                  onLoadedMetadata={(event) => {
                    event.currentTarget.currentTime = Math.max(
                      0,
                      selected.offset_seconds,
                    );
                  }}
                  aria-label="Запись камеры в момент наблюдения"
                />
              ) : (
                <div
                  className="lab-frame-viewer alert-frame-viewer"
                  ref={frameViewer}
                  onClick={(event) => {
                    if (
                      document.fullscreenElement === frameViewer.current &&
                      (event.target === event.currentTarget ||
                        event.target === frameViewport.current)
                    )
                      void document.exitFullscreen();
                  }}
                >
                  <div className="lab-view-toolbar">
                    <fieldset className="lab-display-controls">
                      <legend>Отображение кадра</legend>
                      <label>
                        <input
                          type="checkbox"
                          checked={showFrames}
                          onChange={(event) =>
                            setShowFrames(event.target.checked)
                          }
                        />
                        Показывать рамки
                      </label>
                      <label>
                        <input
                          type="checkbox"
                          checked={showLabels}
                          onChange={(event) =>
                            setShowLabels(event.target.checked)
                          }
                        />
                        Показывать метки классов
                      </label>
                    </fieldset>
                    <div
                      className="lab-zoom-controls"
                      aria-label="Масштаб кадра свидетельства"
                    >
                      <button
                        type="button"
                        className="icon-button"
                        aria-label="Уменьшить кадр"
                        disabled={zoom === 100}
                        onClick={() => changeZoom(zoom - 25)}
                      >
                        <ZoomOut size={19} />
                      </button>
                      <input
                        type="range"
                        min="100"
                        max="400"
                        step="25"
                        value={zoom}
                        aria-label="Масштаб кадра свидетельства"
                        onChange={(event) =>
                          changeZoom(Number(event.target.value))
                        }
                      />
                      <output>{zoom}%</output>
                      <button
                        type="button"
                        className="icon-button"
                        aria-label="Увеличить кадр"
                        disabled={zoom === 400}
                        onClick={() => changeZoom(zoom + 25)}
                      >
                        <ZoomIn size={19} />
                      </button>
                      <button
                        type="button"
                        className="icon-button"
                        aria-label="Сбросить масштаб кадра"
                        disabled={zoom === 100}
                        onClick={() => changeZoom(100)}
                      >
                        <RotateCcw size={18} />
                      </button>
                    </div>
                  </div>
                  <p className="lab-wheel-help">
                    Колесо: вверх/вниз · Ctrl + колесо: масштаб · Shift +
                    колесо: влево/вправо
                  </p>
                  <div className="lab-frame-viewport" ref={frameViewport}>
                    <button
                      type="button"
                      className="icon-button alert-frame-fullscreen"
                      aria-label={
                        frameFullscreen
                          ? "Выйти из полноэкранного просмотра кадра"
                          : "Открыть кадр на весь экран"
                      }
                      aria-pressed={frameFullscreen}
                      onClick={() => void toggleFrameFullscreen()}
                    >
                      {frameFullscreen ? (
                        <Minimize2 size={19} />
                      ) : (
                        <Maximize2 size={19} />
                      )}
                    </button>
                    <div
                      className="lab-frame"
                      style={{ width: `${zoom}%` }}
                      onClick={() => {
                        if (!frameFullscreen) void toggleFrameFullscreen();
                      }}
                    >
                      <img
                        src={selected.image_url}
                        alt={`Кадр наблюдения: ${date(selected.captured_at)}`}
                      />
                      {(showFrames || showLabels) && (
                        <FrameDetections
                          detections={selected.detections.filter(
                            (d) => !hiddenClassIds.has(d.class_id),
                          )}
                          showFrames={showFrames}
                          showLabels={showLabels}
                          nameForClass={className}
                          colorForClass={equipmentColor}
                        />
                      )}
                    </div>
                  </div>
                </div>
              )}
              {!showVideo && (
                <>
                  <h4>Обнаружено в кадре: {selected.detections.length}</h4>
                  {detectionGroups.length ? (
                    <ul className="lab-detection-groups evidence-class-filter">
                      {detectionGroups.map(({ classId, detections }) => (
                        <li
                          key={classId}
                          className={
                            hiddenClassIds.has(classId) ? "is-hidden" : ""
                          }
                        >
                          <label>
                            <input
                              type="checkbox"
                              checked={!hiddenClassIds.has(classId)}
                              aria-label={`Показывать класс «${className(classId)}» на кадре свидетельства`}
                              onChange={(event) =>
                                setClassVisible(classId, event.target.checked)
                              }
                            />
                            <i
                              style={{ background: equipmentColor(classId) }}
                            />
                            <span>{className(classId)}</span>
                          </label>
                          <span className="lab-class-confidences">
                            {detections
                              .map(
                                (detection) =>
                                  `${Math.round(detection.confidence * 100)}%`,
                              )
                              .join(", ")}
                          </span>
                          <strong
                            aria-label={`Количество объектов: ${detections.length}`}
                          >
                            ×{detections.length}
                          </strong>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="hint">
                      Модель не обнаружила технику в этом кадре.
                    </p>
                  )}
                </>
              )}
              <p className="hint">
                {date(selected.captured_at)} · от начала записи:{" "}
                {duration(selected.offset_seconds)}
                {selected.model.demo ? " · Демонстрационная заглушка" : ""}
              </p>
              {data?.video_url ? (
                <button
                  className="button secondary"
                  onClick={() => setShowVideo((v) => !v)}
                >
                  {showVideo ? "Показать кадр" : "Посмотреть запись"}
                </button>
              ) : (
                <p className="hint">
                  Доступны сохранённые кадры. Исходная видеозапись для этого
                  источника не сохранена.
                </p>
              )}
            </>
          ) : (
            !error && (
              <p>
                {data
                  ? "Сохранённые кадры для этого эпизода не найдены."
                  : "Загрузка свидетельств…"}
              </p>
            )
          )}
          <div className="evidence-frames" aria-label="Кадры эпизода">
            {frames.map((frame, i) => (
              <button
                key={frame.id}
                className={selected?.id === frame.id ? "active" : ""}
                onClick={() => setSelected(frame)}
                aria-label={`Кадр ${i + 1}, ${duration(frame.offset_seconds)}`}
              >
                <img loading="lazy" src={frame.image_url} alt="" />
                <span>{exactFrameTime(frame.captured_at)}</span>
                <small>От начала: {duration(frame.offset_seconds)}</small>
                {frame.id === alert.details.evidence_id && (
                  <small>Основание сигнала</small>
                )}
              </button>
            ))}
          </div>
          {data?.next_offset !== null && data?.next_offset !== undefined && (
            <button
              className="text-button"
              onClick={() => setOffset(data.next_offset!)}
            >
              Ещё кадры
            </button>
          )}
        </div>
      )}
      {keyframeIndex !== null &&
        data?.keyframes[keyframeIndex] &&
        createPortal(
          <div
            className="evidence-keyframe-backdrop"
            role="dialog"
            aria-modal="true"
            aria-label="Полноэкранный ключевой кадр"
            onMouseDown={(event) => {
              if (event.target === event.currentTarget) setKeyframeIndex(null);
            }}
          >
            <button
              type="button"
              className="evidence-keyframe-close"
              aria-label="Закрыть кадр"
              onClick={() => setKeyframeIndex(null)}
            >
              ×
            </button>
            <button
              type="button"
              className="evidence-keyframe-arrow"
              aria-label="Предыдущий кадр"
              disabled={keyframeIndex === 0}
              onClick={() => setKeyframeIndex(keyframeIndex - 1)}
            >
              ‹
            </button>
            <figure>
              <div className="evidence-keyframe-image">
                <img src={keyframe!.image_url} alt="Ключевой кадр отклонения" />
                <FrameDetections
                  detections={keyframe!.detections.filter(
                    (item) => !hiddenClassIds.has(item.class_id),
                  )}
                  showFrames={showFrames}
                  showLabels={showLabels}
                  nameForClass={className}
                  colorForClass={equipmentColor}
                />
              </div>
              <figcaption>
                {exactFrameTime(keyframe!.captured_at)} · кадр{" "}
                {keyframeIndex + 1} из {data.keyframes.length}
              </figcaption>
              <div className="evidence-keyframe-controls">
                <label>
                  <input
                    type="checkbox"
                    checked={showFrames}
                    onChange={(event) => setShowFrames(event.target.checked)}
                  />{" "}
                  Рамки
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={showLabels}
                    onChange={(event) => setShowLabels(event.target.checked)}
                  />{" "}
                  Названия
                </label>
                {keyframeClasses.map((classId) => (
                  <label key={classId}>
                    <input
                      type="checkbox"
                      checked={!hiddenClassIds.has(classId)}
                      onChange={(event) =>
                        setClassVisible(classId, event.target.checked)
                      }
                    />
                    {className(classId)}
                  </label>
                ))}
              </div>
            </figure>
            <button
              type="button"
              className="evidence-keyframe-arrow"
              aria-label="Следующий кадр"
              disabled={keyframeIndex === data.keyframes.length - 1}
              onClick={() => setKeyframeIndex(keyframeIndex + 1)}
            >
              ›
            </button>
          </div>,
          document.body,
        )}
    </div>
  );
}
