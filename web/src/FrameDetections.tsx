import { useEffect, useRef, useState } from "react";

export type FrameDetection = {
  class_id: string;
  confidence: number;
  bbox: number[];
  observed?: boolean;
  missed_frames?: number;
  activity?: "working" | "idle" | "unknown";
  activity_basis?: string;
};

const activityReasons: Record<string, string> = {
  vehicle_motion: "Наблюдается движение машины",
  visible_mechanism_motion: "Наблюдается движение рабочего механизма",
  visible_inactivity: "Подтверждена визуальная неподвижность",
  recent_motion: "Работа продолжена после недавнего подтверждения движения",
  idle_pending: "Ещё недостаточно непрерывного наблюдения для простоя",
  idle_recheck: "После возвращения машины движение проверяется повторно",
  motion_pending: "Движение требует подтверждения",
  video_gap: "Разрыв последовательности кадров",
  stale_detection: "Нет свежего подтверждения детекции",
  camera_motion: "Камера сместилась",
  camera_registration_uncertain: "Не удалось проверить устойчивость камеры",
  truncated_equipment: "Машина или рабочий механизм обрезаны краем кадра",
  equipment_occluded: "Машина перекрыта другой техникой",
  roi_too_small: "Машина слишком мала в кадре",
  roi_unusable: "Недостаточно света или деталей в области машины",
  roi_changed: "Область наблюдения существенно изменилась",
  roi_motion_uncertain: "Изменение изображения нельзя уверенно оценить",
  activity_not_visually_observable:
    "Режим работы этого типа техники визуально неразличим",
  track_grace: "Детекция временно потеряна",
  visual_track_grace:
    "Детекция потеряна, но область машины остаётся устойчивой",
  poor_frame_quality: "Кадр непригоден для оценки активности",
  insufficient_video: "Недостаточно видеонаблюдения",
};

type Rect = { x: number; y: number; width: number; height: number };
const overlaps = (a: Rect, b: Rect) =>
  a.x < b.x + b.width + 4 &&
  a.x + a.width + 4 > b.x &&
  a.y < b.y + b.height + 4 &&
  a.y + a.height + 4 > b.y;

export function FrameDetections({
  detections,
  showFrames,
  showLabels,
  nameForClass,
  colorForClass,
}: {
  detections: FrameDetection[];
  showFrames: boolean;
  showLabels: boolean;
  nameForClass: (id: string) => string;
  colorForClass: (id: string) => string;
}) {
  const layer = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    if (!layer.current) return;
    const observer = new ResizeObserver(([entry]) =>
      setSize({
        width: entry.contentRect.width,
        height: entry.contentRect.height,
      }),
    );
    observer.observe(layer.current);
    return () => observer.disconnect();
  }, []);
  const occupied: Rect[] = [];
  const context =
    typeof document === "undefined"
      ? null
      : document.createElement("canvas").getContext("2d");
  if (context) context.font = "13px sans-serif";
  const labels = detections
    .filter((detection) => detection.observed !== false)
    .map((detection, index) => {
      const text =
        nameForClass(detection.class_id) +
        " · " +
        Math.round(detection.confidence * 100) +
        "%";
      const [left, top, right, bottom] = detection.bbox;
      const width = Math.min(
        220,
        size.width,
        Math.ceil(context?.measureText(text).width || text.length * 8) + 18,
      );
      const height = 25;
      const x1 = left * size.width;
      const x2 = right * size.width;
      const y1 = top * size.height;
      const y2 = bottom * size.height;
      const candidates = [
        { x: x1, y: y1 - height - 5 },
        { x: x1, y: y2 + 5 },
        { x: x2 + 5, y: y1 },
        { x: x1 - width - 5, y: y1 },
      ];
      let position: Rect | null = null;
      if (size.width >= 24 && size.height >= 25) {
        for (const candidate of candidates) {
          const rect = {
            x: Math.max(0, Math.min(size.width - width, candidate.x)),
            y: Math.max(0, Math.min(size.height - height, candidate.y)),
            width,
            height,
          };
          if (!occupied.some((other) => overlaps(rect, other))) {
            position = rect;
            occupied.push(rect);
            break;
          }
        }
      }
      const activity =
        detection.activity === "idle"
          ? "Простаивает"
          : detection.activity === "working"
            ? "Работает"
            : "Неопределённо";
      const reason =
        activityReasons[detection.activity_basis || ""] ||
        "Недостаточно видеонаблюдения";
      const title = detection.activity
        ? `${text} · ${activity}: ${reason}`
        : text;
      return { detection, index, text, title, position };
    });
  return (
    <div className="lab-detection-layer" ref={layer}>
      {labels.map(({ detection, index, title }) => (
        <div
          key={index}
          className={"lab-detection" + (showFrames ? " show-frame" : "")}
          style={{
            left: String(detection.bbox[0] * 100) + "%",
            top: String(detection.bbox[1] * 100) + "%",
            width: String((detection.bbox[2] - detection.bbox[0]) * 100) + "%",
            height: String((detection.bbox[3] - detection.bbox[1]) * 100) + "%",
            borderColor: colorForClass(detection.class_id),
            pointerEvents: showFrames ? "auto" : "none",
          }}
          title={title}
        />
      ))}
      {showLabels &&
        labels.map(({ detection, index, text, title, position }) =>
          position ? (
            <span
              key={index}
              className="lab-detection-label"
              style={{
                left: position.x,
                top: position.y,
                width: position.width,
                background: colorForClass(detection.class_id),
              }}
              title={title}
            >
              {text}
            </span>
          ) : null,
        )}
    </div>
  );
}
