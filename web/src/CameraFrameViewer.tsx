import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Maximize2,
  Minimize2,
  RotateCcw,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import type { Catalog, Monitor } from "./api";
import { FrameDetections } from "./FrameDetections";
import { equipmentColor } from "./PresenceCharts";
import "./detector.css";

export function CameraFrameViewer({
  card,
  catalog,
  onClose,
}: {
  card: Monitor["cards"][number];
  catalog: Catalog;
  onClose: () => void;
}) {
  const [zoom, setZoom] = useState(100);
  const [fullscreen, setFullscreen] = useState(false);
  const dialog = useRef<HTMLElement>(null);
  const [showFrames, setShowFrames] = useState(true);
  const [showLabels, setShowLabels] = useState(true);
  const [viewportSize, setViewportSize] = useState({ width: 0, height: 0 });
  const [imageSize, setImageSize] = useState({ width: 0, height: 0 });
  const viewport = useRef<HTMLDivElement>(null);
  const zoomAnchor = useRef<{
    x: number;
    y: number;
    fractionX: number;
    fractionY: number;
  } | null>(null);
  const drag = useRef<{
    x: number;
    y: number;
    left: number;
    top: number;
  } | null>(null);
  const imageUrl = card.observation
    ? "/api/v1/evidence/" + card.observation.id
    : card.source.preview_url || "";
  const time = card.observation?.captured_at
    ? new Intl.DateTimeFormat("ru-RU", {
        timeZone: "Europe/Moscow",
        dateStyle: "short",
        timeStyle: "medium",
      }).format(new Date(card.observation.captured_at))
    : "Время кадра не задано";

  useEffect(() => {
    const current = viewport.current;
    if (!current) return;
    const observer = new ResizeObserver(([entry]) =>
      setViewportSize({
        width: entry.contentRect.width,
        height: entry.contentRect.height,
      }),
    );
    observer.observe(current);
    return () => observer.disconnect();
  }, []);
  const fitWidth =
    imageSize.width &&
    imageSize.height &&
    viewportSize.width &&
    viewportSize.height
      ? Math.min(
          viewportSize.width / imageSize.width,
          viewportSize.height / imageSize.height,
        ) * imageSize.width
      : viewportSize.width;

  useLayoutEffect(() => {
    const current = viewport.current;
    const anchor = zoomAnchor.current;
    if (!current || !anchor) return;
    current.scrollLeft = anchor.fractionX * current.scrollWidth - anchor.x;
    current.scrollTop = anchor.fractionY * current.scrollHeight - anchor.y;
    zoomAnchor.current = null;
  }, [zoom]);

  function changeZoom(nextValue: number, anchor?: { x: number; y: number }) {
    const next = Math.max(100, Math.min(400, nextValue));
    const current = viewport.current;
    if (current && next !== zoom) {
      const x = anchor?.x ?? current.clientWidth / 2;
      const y = anchor?.y ?? current.clientHeight / 2;
      zoomAnchor.current = {
        x,
        y,
        fractionX: (current.scrollLeft + x) / Math.max(1, current.scrollWidth),
        fractionY: (current.scrollTop + y) / Math.max(1, current.scrollHeight),
      };
    }
    setZoom(next);
  }

  useEffect(() => {
    const current = viewport.current;
    if (!current) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      if (event.shiftKey) {
        current.scrollLeft += event.deltaY;
        return;
      }
      const bounds = current.getBoundingClientRect();
      changeZoom(zoom + (event.deltaY < 0 ? 25 : -25), {
        x: event.clientX - bounds.left,
        y: event.clientY - bounds.top,
      });
    };
    current.addEventListener("wheel", onWheel, { passive: false });
    return () => current.removeEventListener("wheel", onWheel);
  }, [zoom]);

  useEffect(() => {
    const syncFullscreen = () =>
      setFullscreen(document.fullscreenElement === dialog.current);
    document.addEventListener("fullscreenchange", syncFullscreen);
    return () =>
      document.removeEventListener("fullscreenchange", syncFullscreen);
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return createPortal(
    <div
      className="camera-frame-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        ref={dialog}
        className="camera-frame-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={"Кадр источника " + card.source.name}
      >
        <header className="camera-frame-header">
          <div>
            <strong>{card.source.name}</strong>
            <small>{time}</small>
          </div>
          <button
            type="button"
            className="icon-button"
            aria-label="Закрыть кадр"
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </header>
        <div className="camera-frame-toolbar">
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
            Метки
          </label>
          <div className="camera-frame-zoom">
            <button
              type="button"
              aria-label={
                fullscreen
                  ? "Выйти из полного экрана"
                  : "Открыть на полный экран"
              }
              onClick={() => {
                if (fullscreen) void document.exitFullscreen();
                else void dialog.current?.requestFullscreen();
              }}
            >
              {fullscreen ? <Minimize2 size={18} /> : <Maximize2 size={18} />}
            </button>
            <button
              type="button"
              aria-label="Уменьшить кадр"
              disabled={zoom === 100}
              onClick={() => changeZoom(zoom - 25)}
            >
              <ZoomOut size={18} />
            </button>
            <output>{zoom}%</output>
            <button
              type="button"
              aria-label="Увеличить кадр"
              disabled={zoom === 400}
              onClick={() => changeZoom(zoom + 25)}
            >
              <ZoomIn size={18} />
            </button>
            <button
              type="button"
              aria-label="Сбросить масштаб"
              disabled={zoom === 100}
              onClick={() => changeZoom(100)}
            >
              <RotateCcw size={17} />
            </button>
          </div>
        </div>
        <div
          className="camera-frame-viewport"
          ref={viewport}
          onPointerDown={(event) => {
            if (event.button !== 0) return;
            const current = viewport.current;
            if (!current) return;
            drag.current = {
              x: event.clientX,
              y: event.clientY,
              left: current.scrollLeft,
              top: current.scrollTop,
            };
            current.setPointerCapture(event.pointerId);
          }}
          onPointerMove={(event) => {
            const current = viewport.current;
            const start = drag.current;
            if (!current || !start) return;
            current.scrollLeft = start.left - (event.clientX - start.x);
            current.scrollTop = start.top - (event.clientY - start.y);
          }}
          onPointerUp={() => {
            drag.current = null;
          }}
          onPointerCancel={() => {
            drag.current = null;
          }}
        >
          <div
            className="camera-frame-canvas"
            style={{
              width: fitWidth ? `${(fitWidth * zoom) / 100}px` : "100%",
            }}
          >
            <img
              src={imageUrl}
              alt={"Кадр источника " + card.source.name}
              onLoad={(event) =>
                setImageSize({
                  width: event.currentTarget.naturalWidth,
                  height: event.currentTarget.naturalHeight,
                })
              }
            />
            {card.observation && (showFrames || showLabels) && (
              <FrameDetections
                detections={card.observation.detections.filter(
                  (detection) => detection.observed !== false,
                )}
                showFrames={showFrames}
                showLabels={showLabels}
                nameForClass={(id) =>
                  catalog.classes.find((item) => String(item.id) === id)
                    ?.name || "Класс " + id
                }
                colorForClass={equipmentColor}
              />
            )}
          </div>
        </div>
        <p className="camera-frame-help">
          Колесо — масштаб около курсора; перетаскивание — перемещение кадра;
          Shift + колесо — горизонтальная прокрутка.
        </p>
      </section>
    </div>,
    document.body,
  );
}
