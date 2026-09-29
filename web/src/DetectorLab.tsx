import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Images,
  LoaderCircle,
  Maximize2,
  Minimize2,
  Pause,
  Play,
  RotateCcw,
  ScanLine,
  SlidersHorizontal,
  Upload,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { api, post, date, stateName, type Catalog } from "./api";
import { equipmentColor } from "./PresenceCharts";
import { FrameDetections } from "./FrameDetections";
import { InfoButton } from "./InfoButton";
import { bindFrameWheel } from "./frameInteraction";
import "./detector.css";

type Mode = "full" | "frame" | "segment";
type InferenceParameters = {
  confidence: number;
  iou: number;
  max_detections: number;
};
type TilingParameters = {
  enabled: boolean;
  overlap: number;
};
type DetectorSettings = {
  product_mode_name: string;
  defaults: InferenceParameters;
  product: {
    parameters: InferenceParameters;
    revision: number;
    updated_at: string | null;
  };
};
type Detection = { class_id: string; confidence: number; bbox: number[] };
type Frame = {
  id: string;
  sample_index: number;
  offset_seconds: number;
  detections: Detection[];
  image_url: string;
  source_name: string | null;
  quality: { usable: boolean };
};
type Run = {
  id: string;
  name: string;
  kind: string;
  mode: string;
  status: string;
  progress: number;
  processed_frames: number;
  error: string | null;
  created_at: string;
  content_url: string;
  preview_url: string | null;
  start_seconds: number;
  end_seconds: number | null;
  sample_seconds: number;
  model: {
    name?: string;
    sha256?: string;
    providers?: string[];
    supported_classes?: string[];
    tiling?: TilingParameters & { tile_size?: number };
  };
  inference_parameters: Partial<InferenceParameters>;
  tiling: TilingParameters;
  metadata_json: {
    width?: number;
    height?: number;
    duration_seconds?: number;
    frame_count?: number;
    frame_names?: string[];
  };
};
const modeNames: Record<string, string> = {
  image: "Изображение",
  batch: "Пачка кадров",
  full: "Видео",
  frame: "Стоп-кадр",
  segment: "Отрезок видео",
};
const historyKind = (run: Run) =>
  run.kind === "batch" ? "batch" : run.kind === "image" ? "image" : "video";
const fallbackParameters: InferenceParameters = {
  confidence: 0.35,
  iou: 0.7,
  max_detections: 300,
};
const fallbackTiling: TilingParameters = { enabled: false, overlap: 0.2 };
const sameParameters = (
  left: InferenceParameters,
  right: InferenceParameters,
) =>
  left.confidence === right.confidence &&
  left.iou === right.iou &&
  left.max_detections === right.max_detections;
const sameTiling = (left: TilingParameters, right: TilingParameters) =>
  left.enabled === right.enabled && left.overlap === right.overlap;
const seconds = (value: number) =>
  `${Number(value.toFixed(3)).toLocaleString("ru-RU")} с`;
const frameCount = (value: number) => {
  const word =
    value % 10 === 1 && value % 100 !== 11
      ? "кадр"
      : [2, 3, 4].includes(value % 10) && ![12, 13, 14].includes(value % 100)
        ? "кадра"
        : "кадров";
  return `${value} ${word}`;
};
const active = (run: Run | null) =>
  run && ["queued", "running"].includes(run.status);

export function DetectorLab({ catalog }: { catalog: Catalog | null }) {
  const [files, setFiles] = useState<File[]>([]),
    [previews, setPreviews] = useState<string[]>([]);
  const [mode, setMode] = useState<Mode>("full"),
    [start, setStart] = useState(0),
    [end, setEnd] = useState(0);
  const [frameAt, setFrameAt] = useState(0),
    [interval, setSampleInterval] = useState(1),
    [length, setLength] = useState(0);
  const [runs, setRuns] = useState<Run[]>([]),
    [selectedId, setSelectedId] = useState("");
  const [run, setRun] = useState<Run | null>(null),
    [frames, setFrames] = useState<Frame[]>([]);
  const [page, setPage] = useState(0),
    [nextPage, setNextPage] = useState<number | null>(null),
    [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false),
    [showFrames, setShowFrames] = useState(true),
    [showLabels, setShowLabels] = useState(true),
    [zoom, setZoom] = useState(100),
    [frameFullscreen, setFrameFullscreen] = useState(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [settings, setSettings] = useState<DetectorSettings | null>(null),
    [parameters, setParameters] =
      useState<InferenceParameters>(fallbackParameters),
    [pageParameters, setPageParameters] =
      useState<InferenceParameters>(fallbackParameters);
  const [tiling, setTiling] = useState<TilingParameters>(fallbackTiling),
    [pageTiling, setPageTiling] = useState<TilingParameters>(fallbackTiling);
  const [confirmProduct, setConfirmProduct] = useState(false),
    [savingSettings, setSavingSettings] = useState(false),
    [notice, setNotice] = useState("");
  const [hiddenClassIds, setHiddenClassIds] = useState<Set<string>>(new Set());
  const [health, setHealth] = useState<{
    model: { ready: boolean; name?: string };
  } | null>(null);
  const video = useRef<HTMLVideoElement>(null);
  const frameViewer = useRef<HTMLDivElement>(null);
  const frameViewport = useRef<HTMLDivElement>(null);
  const resultsPanel = useRef<HTMLElement>(null);
  const settingsInitialized = useRef(false);
  const file = files[0] || null;
  const preview = previews[0] || "";
  const isBatch = files.length > 1;
  const isImage = file ? /\.(png|jpe?g|webp)$/i.test(file.name) : false;
  const names = (id: string) =>
    catalog?.classes.find((c) => String(c.id) === id)?.name || `Класс ${id}`;
  const selected = frames[index];
  const detectionGroups = selected
    ? Array.from(
        selected.detections.reduce((groups, detection) => {
          const current = groups.get(detection.class_id) || [];
          current.push(detection);
          groups.set(detection.class_id, current);
          return groups;
        }, new Map<string, Detection[]>()),
      )
        .map(([classId, detections]) => ({ classId, detections }))
        .sort((left, right) =>
          names(left.classId).localeCompare(names(right.classId), "ru"),
        )
    : [];
  useEffect(() => {
    if (!files.length) {
      setPreviews([]);
      return;
    }
    const urls = files.map((item) => URL.createObjectURL(item));
    setPreviews(urls);
    return () => urls.forEach(URL.revokeObjectURL);
  }, [files]);
  useEffect(() => {
    const controller = new AbortController();
    const refresh = () =>
      Promise.all([
        api<Run[]>("/detector/runs", { signal: controller.signal }),
        api<{ model: { ready: boolean; name?: string } }>("/health", {
          signal: controller.signal,
        }),
        api<DetectorSettings>("/detector/settings", {
          signal: controller.signal,
        }),
      ])
        .then(([list, status, modelSettings]) => {
          setRuns(list);
          setHealth(status);
          setSettings(modelSettings);
          if (!settingsInitialized.current) {
            setParameters(modelSettings.defaults);
            setPageParameters(modelSettings.defaults);
            settingsInitialized.current = true;
          }
        })
        .catch((e) => {
          if (!controller.signal.aborted) setError(e.message);
        });
    void refresh();
    const timer = setInterval(refresh, 3000);
    return () => {
      clearInterval(timer);
      controller.abort();
    };
  }, []);
  useEffect(() => {
    setPlaying(false);
    setFrames([]);
    setRun(null);
    setIndex(0);
    setPage(0);
    setZoom(100);
    setHiddenClassIds(new Set());
  }, [selectedId]);
  useEffect(() => {
    if (!selectedId) return;
    const controller = new AbortController();
    const refresh = async () => {
      try {
        const [info, data] = await Promise.all([
          api<Run>(`/detector/runs/${selectedId}`, {
            signal: controller.signal,
          }),
          api<{ frames: Frame[]; next_offset: number | null }>(
            `/detector/runs/${selectedId}/frames?offset=${page}`,
            { signal: controller.signal },
          ),
        ]);
        if (controller.signal.aborted) return;
        setRun(info);
        setFrames(data.frames);
        setNextPage(data.next_offset);
      } catch (e) {
        if (!controller.signal.aborted) setError((e as Error).message);
      }
    };
    void refresh();
    const timer = setInterval(refresh, 2000);
    return () => {
      clearInterval(timer);
      controller.abort();
    };
  }, [selectedId, page]);
  useEffect(() => {
    if (!playing || !frames.length) return;
    const timer = setInterval(() => {
      if (index < frames.length - 1) setIndex(index + 1);
      else if (nextPage != null) {
        setPage(nextPage);
        setIndex(0);
        setPlaying(false);
      } else setPlaying(false);
    }, 1000);
    return () => clearInterval(timer);
  }, [playing, index, frames.length, nextPage]);
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
  }, [zoom, selected?.id]);

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
      setError("Браузер не разрешил открыть изображение на весь экран.");
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!files.length) return;
    setError("");
    if (
      !isImage &&
      !isBatch &&
      mode === "segment" &&
      (end <= start || (length > 0 && end > length))
    ) {
      setError("Укажите конец отрезка после начала и в пределах видео.");
      return;
    }
    if (
      !isImage &&
      !isBatch &&
      mode === "frame" &&
      length > 0 &&
      frameAt >= length
    ) {
      setError("Стоп-кадр должен быть раньше конца видео.");
      return;
    }
    setBusy(true);
    try {
      const data = new FormData();
      files.forEach((item) => data.append("file", item));
      data.set("mode", isBatch ? "batch" : isImage ? "image" : mode);
      data.set("sample_seconds", String(interval));
      data.set("confidence", String(pageParameters.confidence));
      data.set("iou", String(pageParameters.iou));
      data.set("max_detections", String(pageParameters.max_detections));
      data.set("tiled", String(pageTiling.enabled));
      data.set("tile_overlap", String(pageTiling.overlap));
      data.set(
        "start_seconds",
        String(
          isImage || isBatch || mode === "full"
            ? 0
            : mode === "frame"
              ? frameAt
              : start,
        ),
      );
      if (!isImage && !isBatch && mode === "segment")
        data.set("end_seconds", String(end));
      const result = await api<Run>("/detector/runs", {
        method: "POST",
        body: data,
      });
      setSelectedId(result.id);
      setRun(result);
      setRuns((list) => [result, ...list]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function applyProductSettings() {
    if (!settings || sameParameters(parameters, settings.product.parameters))
      return;
    setSavingSettings(true);
    setError("");
    try {
      const changed = await api<DetectorSettings>("/detector/settings", {
        method: "PUT",
        body: JSON.stringify({
          ...parameters,
          expected_revision: settings.product.revision,
        }),
      });
      setSettings(changed);
      setConfirmProduct(false);
      setNotice(
        `Настройки режима «${changed.product_mode_name}» сохранены для новых задач.`,
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSavingSettings(false);
    }
  }

  const productChanged = Boolean(
    settings && !sameParameters(parameters, settings.product.parameters),
  );
  const pageChanged =
    !sameParameters(parameters, pageParameters) ||
    !sameTiling(tiling, pageTiling);
  const defaultsChanged = Boolean(
    settings &&
      (!sameParameters(parameters, settings.defaults) ||
        !sameTiling(tiling, fallbackTiling)),
  );
  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">КОМПЬЮТЕРНОЕ ЗРЕНИЕ</div>
          <h1>Проверка детектора</h1>
          <p>
            Загрузите файл и проверьте, какую технику видит модель. Результаты
            сохраняются отдельно от объектов и планов.
          </p>
        </div>
      </div>
      {error && (
        <div className="banner danger" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className="banner success" role="status">
          {notice}
        </div>
      )}
      <div className="detector-layout">
        <section className="panel detector-input">
          <div className="panel-help-heading">
            <h2>
              <Upload size={22} /> Новый запуск
            </h2>
            <InfoButton title="Новый запуск детектора">
              <p>
                Проверка принимает одно видео, изображение или до 100 кадров как
                одну пачку. Результат не влияет на мониторинг объектов.
              </p>
            </InfoButton>
          </div>
          <p className="hint">
            {!health
              ? "Проверяем доступность модели…"
              : health.model.ready
                ? `Модель: ${health.model.name}`
                : "Детектор недоступен. Проверьте пакет модели на сервере."}
          </p>
          <form onSubmit={submit}>
            <label>
              Изображение, видео или пачка кадров
              <input
                type="file"
                required
                multiple
                accept=".png,.jpg,.jpeg,.webp,.mp4,.mov,.mkv,.avi"
                onChange={(e) => {
                  const selected = Array.from(e.target.files || []);
                  const allImages = selected.every((item) =>
                    /\.(png|jpe?g|webp)$/i.test(item.name),
                  );
                  if (selected.length > 100) {
                    setFiles([]);
                    setError("За один запуск можно загрузить до 100 кадров.");
                    e.currentTarget.value = "";
                    return;
                  }
                  if (selected.length > 1 && !allImages) {
                    setFiles([]);
                    setError(
                      "Пачка должна содержать только изображения PNG, JPG или WebP.",
                    );
                    e.currentTarget.value = "";
                    return;
                  }
                  setFiles(selected);
                  setLength(0);
                  setStart(0);
                  setFrameAt(0);
                  setEnd(0);
                  setError("");
                }}
              />
            </label>
            {isBatch && (
              <div
                className="lab-upload-batch"
                aria-label="Выбранная пачка кадров"
              >
                {previews.slice(0, 8).map((url, itemIndex) => (
                  <img
                    key={url}
                    src={url}
                    alt={`Кадр ${itemIndex + 1}: ${files[itemIndex].name}`}
                  />
                ))}
                <strong>{frameCount(files.length)}</strong>
              </div>
            )}
            {preview &&
              !isBatch &&
              (isImage ? (
                <img
                  className="lab-preview"
                  src={preview}
                  alt="Изображение для проверки"
                />
              ) : (
                <video
                  className="lab-preview"
                  key={preview}
                  ref={video}
                  src={preview}
                  controls
                  preload="metadata"
                  onLoadedMetadata={(e) => {
                    const n = e.currentTarget.duration;
                    if (Number.isFinite(n)) {
                      setLength(n);
                      setEnd(Number(n.toFixed(3)));
                    }
                  }}
                />
              ))}
            {file && !isImage && !isBatch && (
              <>
                <label>
                  Режим обработки
                  <select
                    value={mode}
                    onChange={(e) => setMode(e.target.value as Mode)}
                  >
                    <option value="full">Видео</option>
                    <option value="frame">Стоп-кадр</option>
                    <option value="segment">Отрезок видео</option>
                  </select>
                </label>
                {length > 0 && (
                  <p className="hint">Длительность: {seconds(length)}</p>
                )}
                {mode === "frame" && (
                  <>
                    <label>
                      Позиция стоп-кадра, секунды
                      <input
                        type="number"
                        min="0"
                        step="0.001"
                        required
                        value={frameAt}
                        onChange={(e) => setFrameAt(e.target.valueAsNumber)}
                      />
                    </label>
                    <button
                      type="button"
                      className="button secondary"
                      onClick={() =>
                        setFrameAt(
                          Number((video.current?.currentTime || 0).toFixed(3)),
                        )
                      }
                    >
                      Взять текущий кадр плеера
                    </button>
                  </>
                )}
                {mode === "segment" && (
                  <div className="lab-range">
                    <label>
                      Начало, секунды
                      <input
                        type="number"
                        min="0"
                        step="0.001"
                        required
                        value={start}
                        onChange={(e) => setStart(e.target.valueAsNumber)}
                      />
                    </label>
                    <label>
                      Конец, секунды
                      <input
                        type="number"
                        min="0.001"
                        step="0.001"
                        required
                        value={end}
                        onChange={(e) => setEnd(e.target.valueAsNumber)}
                      />
                    </label>
                    <button
                      type="button"
                      className="text-button"
                      onClick={() =>
                        setStart(
                          Number((video.current?.currentTime || 0).toFixed(3)),
                        )
                      }
                    >
                      Начало из плеера
                    </button>
                    <button
                      type="button"
                      className="text-button"
                      onClick={() =>
                        setEnd(
                          Number((video.current?.currentTime || 0).toFixed(3)),
                        )
                      }
                    >
                      Конец из плеера
                    </button>
                  </div>
                )}
                {mode !== "frame" && (
                  <label>
                    Обрабатывать один кадр каждые
                    <select
                      value={interval}
                      onChange={(e) =>
                        setSampleInterval(Number(e.target.value))
                      }
                    >
                      {[1, 2, 5, 10, 30, 60].map((n) => (
                        <option key={n} value={n}>
                          {n} с
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <p className="hint">
                  Если браузер не воспроизводит кодек, время можно указать
                  вручную. Видео обрабатывается с выбранным шагом; рамки
                  показываются на обработанных кадрах.
                </p>
              </>
            )}
            <details className="lab-advanced">
              <summary>
                <SlidersHorizontal size={18} /> Расширенные параметры модели
              </summary>
              <p className="hint">
                Измените значения и отдельно примените их для новых запусков на
                этой странице или для режима «
                {settings?.product_mode_name || "Фоновый мониторинг объектов"}»
                . Изменение фонового режима потребует подтверждения.
              </p>
              <div className="lab-parameter-grid">
                <label>
                  <span>Порог уверенности (confidence)</span>
                  <input
                    type="number"
                    min="0.01"
                    max="1"
                    step="0.01"
                    required
                    value={parameters.confidence}
                    onChange={(e) => {
                      const value = e.target.valueAsNumber;
                      if (Number.isFinite(value))
                        setParameters((current) => ({
                          ...current,
                          confidence: value,
                        }));
                    }}
                  />
                </label>
                <label>
                  <span>Перекрытие NMS (IoU)</span>
                  <input
                    type="number"
                    min="0.01"
                    max="1"
                    step="0.01"
                    required
                    value={parameters.iou}
                    onChange={(e) => {
                      const value = e.target.valueAsNumber;
                      if (Number.isFinite(value))
                        setParameters((current) => ({
                          ...current,
                          iou: value,
                        }));
                    }}
                  />
                </label>
                <label>
                  <span>Максимум рамок на кадр</span>
                  <input
                    type="number"
                    min="1"
                    max="1000"
                    step="1"
                    required
                    value={parameters.max_detections}
                    onChange={(e) => {
                      const value = e.target.valueAsNumber;
                      if (Number.isFinite(value))
                        setParameters((current) => ({
                          ...current,
                          max_detections: value,
                        }));
                    }}
                  />
                </label>
              </div>
              <div className={`lab-tiling${tiling.enabled ? " enabled" : ""}`}>
                <label className="lab-tiling-toggle">
                  <input
                    type="checkbox"
                    checked={tiling.enabled}
                    onChange={(e) =>
                      setTiling((current) => ({
                        ...current,
                        enabled: e.target.checked,
                      }))
                    }
                  />
                  <span>
                    <strong>Обрабатывать кадр по тайлам</strong>
                    <small>
                      Без сжатия всего кадра: модель последовательно проверит
                      перекрывающиеся фрагменты исходного изображения.
                    </small>
                  </span>
                </label>
                <label className="lab-tile-overlap">
                  Перекрытие тайлов
                  <select
                    value={tiling.overlap}
                    disabled={!tiling.enabled}
                    onChange={(e) =>
                      setTiling((current) => ({
                        ...current,
                        overlap: Number(e.target.value),
                      }))
                    }
                  >
                    {[0.1, 0.2, 0.3].map((value) => (
                      <option key={value} value={value}>
                        {value * 100}%
                      </option>
                    ))}
                  </select>
                </label>
                <p className="hint">
                  Размер тайла равен входу модели. Найденные рамки переносятся в
                  координаты исходного кадра и объединяются общим NMS. Режим
                  действует только на этой странице и увеличивает время
                  обработки.
                </p>
              </div>
              <div className="lab-settings-actions">
                <button
                  type="button"
                  className="button secondary"
                  disabled={!defaultsChanged || savingSettings}
                  onClick={() => {
                    if (!settings) return;
                    setParameters(settings.defaults);
                    setTiling(fallbackTiling);
                  }}
                >
                  Установить параметры по умолчанию
                </button>
                <button
                  type="button"
                  className="button primary"
                  disabled={!pageChanged || savingSettings}
                  onClick={() => {
                    setPageParameters({ ...parameters });
                    setPageTiling({ ...tiling });
                    setNotice(
                      "Параметры применены для новых запусков на странице проверки детектора.",
                    );
                  }}
                >
                  Применить для проверки детектора
                </button>
                <button
                  type="button"
                  className="button secondary lab-product-settings"
                  disabled={!productChanged || savingSettings}
                  onClick={() => setConfirmProduct(true)}
                >
                  Применить для фоновой обработки
                </button>
              </div>
            </details>
            <button
              className="button primary lab-submit"
              disabled={!files.length || busy || !health?.model.ready}
            >
              {busy ? (
                <LoaderCircle className="spin" size={18} />
              ) : (
                <ScanLine size={18} />
              )}
              {busy ? "Загрузка файлов…" : "Запустить детектор"}
            </button>
          </form>
        </section>
        <section className="panel detector-results" ref={resultsPanel}>
          <div className="panel-help-heading">
            <h2>Результат проверки</h2>
            <InfoButton title="Результат проверки">
              <p>
                Рамки и метки можно скрывать для просмотра; это не меняет
                сохранённый результат детектора. Количество относится только к
                выбранному кадру и не означает выполненную работу или уникальную
                машину.
              </p>
            </InfoButton>
          </div>
          {!selectedId ? (
            <p className="lab-empty">
              Запустите детектор или откройте предыдущую проверку ниже.
            </p>
          ) : !run ? (
            <p role="status">Загрузка результата…</p>
          ) : (
            <>
              <div className="lab-result-head">
                <div>
                  <h3>{run.name}</h3>
                  <p className="hint">
                    {modeNames[run.mode]} · {stateName[run.status]} · обработано
                    : {frameCount(run.processed_frames)}
                  </p>
                </div>
                {active(run) && (
                  <button
                    className="button secondary"
                    onClick={() => {
                      void post<Run>(`/detector/runs/${run.id}/cancel`)
                        .then(setRun)
                        .catch((e) => setError(e.message));
                    }}
                  >
                    Остановить
                  </button>
                )}
              </div>
              {active(run) && (
                <>
                  <progress
                    aria-label="Ход проверки"
                    max="1"
                    value={run.progress}
                  />
                  <p role="status" className="hint">
                    {run.status === "queued"
                      ? "Ожидание свободного worker"
                      : `${Math.round(run.progress * 100)}%`}
                  </p>
                </>
              )}
              {run.error && (
                <p className="banner danger" role="alert">
                  {run.error}
                </p>
              )}
              {selected && (
                <>
                  <div
                    className="lab-frame-viewer"
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
                        <legend>Отображение</legend>
                        <label>
                          <input
                            type="checkbox"
                            checked={showFrames}
                            onChange={(e) => setShowFrames(e.target.checked)}
                          />
                          Показывать рамки
                        </label>
                        <label>
                          <input
                            type="checkbox"
                            checked={showLabels}
                            onChange={(e) => setShowLabels(e.target.checked)}
                          />
                          Показывать метки классов
                        </label>
                      </fieldset>
                      <div
                        className="lab-zoom-controls"
                        aria-label="Масштаб изображения"
                      >
                        <button
                          type="button"
                          className="icon-button"
                          aria-label="Уменьшить изображение"
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
                          aria-label="Масштаб изображения"
                          onChange={(e) => changeZoom(Number(e.target.value))}
                        />
                        <output>{zoom}%</output>
                        <button
                          type="button"
                          className="icon-button"
                          aria-label="Увеличить изображение"
                          disabled={zoom === 400}
                          onClick={() => changeZoom(zoom + 25)}
                        >
                          <ZoomIn size={19} />
                        </button>
                        <button
                          type="button"
                          className="icon-button"
                          aria-label="Сбросить масштаб"
                          disabled={zoom === 100}
                          onClick={() => changeZoom(100)}
                        >
                          <RotateCcw size={18} />
                        </button>
                        <button
                          type="button"
                          className="icon-button"
                          aria-label={
                            frameFullscreen
                              ? "Выйти из полноэкранного режима"
                              : "Открыть изображение на весь экран"
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
                      </div>
                    </div>
                    <p className="lab-wheel-help">
                      Колесо: вверх/вниз · Ctrl + колесо: масштаб · Shift +
                      колесо: влево/вправо
                    </p>
                    <div className="lab-frame-viewport" ref={frameViewport}>
                      <div
                        className="lab-frame"
                        style={{ width: `${zoom}%` }}
                        onClick={() => {
                          if (!frameFullscreen) void toggleFrameFullscreen();
                        }}
                      >
                        <img
                          src={selected.image_url}
                          alt={
                            selected.source_name
                              ? `Обработанный кадр ${selected.source_name}`
                              : `Обработанный кадр на ${seconds(selected.offset_seconds)}`
                          }
                        />
                        {(showFrames || showLabels) && (
                          <FrameDetections
                            detections={selected.detections.filter(
                              (d) => !hiddenClassIds.has(d.class_id),
                            )}
                            showFrames={showFrames}
                            showLabels={showLabels}
                            nameForClass={names}
                            colorForClass={equipmentColor}
                          />
                        )}
                      </div>
                    </div>
                  </div>
                  <div className="lab-player">
                    <button
                      type="button"
                      className="icon-button"
                      aria-label="Предыдущий кадр"
                      disabled={index === 0}
                      onClick={() => {
                        setPlaying(false);
                        setIndex(index - 1);
                      }}
                    >
                      <ChevronLeft />
                    </button>
                    <button
                      type="button"
                      className="button secondary"
                      disabled={frames.length < 2}
                      onClick={() => setPlaying(!playing)}
                    >
                      {playing ? <Pause size={17} /> : <Play size={17} />}
                      {playing ? "Пауза" : "Просмотр кадров"}
                    </button>
                    <button
                      type="button"
                      className="icon-button"
                      aria-label="Следующий кадр"
                      disabled={index === frames.length - 1}
                      onClick={() => {
                        setPlaying(false);
                        setIndex(index + 1);
                      }}
                    >
                      <ChevronRight />
                    </button>
                    <strong>
                      {selected.source_name || seconds(selected.offset_seconds)}
                    </strong>
                    <span>
                      Кадр {selected.sample_index + 1}
                      {run.kind === "batch" && run.metadata_json.frame_count
                        ? ` из ${run.metadata_json.frame_count}`
                        : ""}
                    </span>
                  </div>
                  <input
                    className="lab-slider"
                    aria-label="Обработанный кадр"
                    type="range"
                    min="0"
                    max={Math.max(0, frames.length - 1)}
                    value={index}
                    onChange={(e) => {
                      setPlaying(false);
                      setIndex(Number(e.target.value));
                    }}
                  />
                  {run.kind === "batch" && frames.length > 1 && (
                    <div
                      className="lab-frame-picker"
                      aria-label="Предпросмотр кадров пачки"
                    >
                      {frames.map((frame, frameIndex) => (
                        <button
                          type="button"
                          key={frame.id}
                          className={frameIndex === index ? "selected" : ""}
                          aria-label={`Открыть кадр ${frameIndex + 1}: ${frame.source_name || "без названия"}`}
                          onClick={() => {
                            setPlaying(false);
                            setIndex(frameIndex);
                          }}
                        >
                          <img src={frame.image_url} loading="lazy" alt="" />
                          <span>
                            {frame.source_name || `Кадр ${frameIndex + 1}`}
                          </span>
                        </button>
                      ))}
                    </div>
                  )}
                  {!selected.quality.usable && (
                    <p className="inline-warning">
                      Низкое качество кадра. Результат требует визуальной
                      проверки.
                    </p>
                  )}
                  <h3>Обнаружено в кадре: {selected.detections.length}</h3>
                  {selected.detections.length ? (
                    <ul className="lab-detection-groups">
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
                              aria-label={`Показывать класс «${names(classId)}» на кадре`}
                              onChange={(event) => {
                                const visible = event.target.checked;
                                setHiddenClassIds((current) => {
                                  const next = new Set(current);
                                  if (visible) next.delete(classId);
                                  else next.add(classId);
                                  return next;
                                });
                              }}
                            />
                            <i
                              style={{ background: equipmentColor(classId) }}
                            />
                            <span>{names(classId)}</span>
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
                  <div className="lab-player">
                    <button
                      className="button secondary"
                      disabled={page === 0}
                      onClick={() => {
                        setPage(Math.max(0, page - 100));
                        setIndex(0);
                        setPlaying(false);
                      }}
                    >
                      Предыдущие 100
                    </button>
                    <button
                      className="button secondary"
                      disabled={nextPage == null}
                      onClick={() => {
                        if (nextPage != null) {
                          setPage(nextPage);
                          setIndex(0);
                          setPlaying(false);
                        }
                      }}
                    >
                      Следующие 100
                    </button>
                  </div>
                </>
              )}
              <p className="hint">
                {run.model.name ||
                  "Модель будет указана после начала обработки"}
                {run.model.providers?.length
                  ? ` · ${run.model.providers.join(", ")}`
                  : ""}
              </p>
              {Object.keys(run.inference_parameters || {}).length > 0 && (
                <p className="hint">
                  Параметры запуска: confidence{" "}
                  {run.inference_parameters.confidence}, NMS IoU{" "}
                  {run.inference_parameters.iou}, максимум рамок{" "}
                  {run.inference_parameters.max_detections}. Обработка:{" "}
                  {run.tiling?.enabled
                    ? `по тайлам${run.model.tiling?.tile_size ? ` ${run.model.tiling.tile_size}\u00d7${run.model.tiling.tile_size}` : ""}, перекрытие ${Math.round(run.tiling.overlap * 100)}%`
                    : "целый кадр"}
                  .
                </p>
              )}
            </>
          )}
        </section>
      </div>
      <section className="panel detector-history">
        <div className="panel-help-heading">
          <h2>Последние проверки</h2>
          <InfoButton title="Последние проверки">
            <p>
              Сохранены 30 последних запусков. Пачка кадров отображается одной
              проверкой; плитка открывает её результат.
            </p>
          </InfoButton>
        </div>
        <div className="lab-history-grid">
          {runs.map((item) => (
            <button
              key={item.id}
              className={`lab-history-card ${historyKind(item)}${selectedId === item.id ? " selected" : ""}`}
              onClick={() => {
                setSelectedId(item.id);
                setError("");
                requestAnimationFrame(() =>
                  resultsPanel.current?.scrollIntoView({
                    behavior: "smooth",
                    block: "start",
                  }),
                );
              }}
            >
              <span className="lab-history-media">
                {item.preview_url ? (
                  <img src={item.preview_url} loading="lazy" alt="" />
                ) : (
                  <span className="lab-history-placeholder">
                    {item.kind === "batch" ? (
                      <Images size={32} />
                    ) : (
                      <ScanLine size={32} />
                    )}
                  </span>
                )}
                <b className={`lab-history-kind ${historyKind(item)}`}>
                  {item.kind === "batch"
                    ? `Группа · ${frameCount(item.metadata_json.frame_count || item.processed_frames)}`
                    : modeNames[item.mode]}
                </b>
              </span>
              <span className="lab-history-copy">
                <strong>{item.name}</strong>
                <small>
                  {date(item.created_at)} · {modeNames[item.mode]}
                </small>
                <span>
                  {stateName[item.status]} · обработано{" "}
                  {frameCount(item.processed_frames)}
                </span>
                <em>
                  {item.kind === "batch"
                    ? "Открыть пачку"
                    : "Открыть результат"}
                </em>
              </span>
            </button>
          ))}
        </div>
        {!runs.length && <p className="hint">Проверок пока нет.</p>}
      </section>
      {confirmProduct && settings && (
        <div className="modal-backdrop" role="presentation">
          <div
            className="modal lab-settings-confirm"
            role="dialog"
            aria-modal="true"
            aria-labelledby="model-settings-title"
          >
            <div className="modal-heading">
              <h2 id="model-settings-title">Изменить настройки модели?</h2>
            </div>
            <p>
              Это изменит настройки работы модели в режиме «
              {settings.product_mode_name}» для новых задач анализа. Применить
              изменения?
            </p>
            <dl>
              <div>
                <dt>Confidence</dt>
                <dd>{parameters.confidence}</dd>
              </div>
              <div>
                <dt>NMS IoU</dt>
                <dd>{parameters.iou}</dd>
              </div>
              <div>
                <dt>Максимум рамок</dt>
                <dd>{parameters.max_detections}</dd>
              </div>
            </dl>
            <div className="form-actions">
              <button
                type="button"
                className="button primary"
                disabled={savingSettings}
                onClick={() => void applyProductSettings()}
              >
                Применить
              </button>
              <button
                type="button"
                className="button secondary"
                disabled={savingSettings}
                onClick={() => {
                  setConfirmProduct(false);
                  setNotice("Настройки фоновой обработки не изменены.");
                }}
              >
                Нет
              </button>
              <button
                type="button"
                className="text-button"
                disabled={savingSettings}
                onClick={() => setConfirmProduct(false)}
              >
                Отмена
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
