import React, { useState, useEffect, useCallback, useRef } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  ArrowUpRight,
  Bell,
  Baby,
  BriefcaseBusiness,
  Building2,
  BookOpen,
  CalendarDays,
  Camera,
  Check,
  Clock3,
  FileSpreadsheet,
  FolderOpen,
  GraduationCap,
  Home,
  Hospital,
  Landmark,
  Layers3,
  LoaderCircle,
  MapPin,
  MoreHorizontal,
  Maximize2,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  Square,
  Trash2,
  Upload,
  X,
  Zap,
  TriangleAlert,
  Play,
  Pause,
  ScanLine,
  History,
  GripVertical,
  ArrowUp,
  ArrowDown,
  ChevronDown,
  ChevronRight,
  Drama,
  Route,
  Trophy,
} from "lucide-react";
import {
  api,
  post,
  date,
  duration,
  stateName,
  type Catalog,
  type Project,
  type Plan,
  type Work,
  type Source,
  type Binding,
  type Region,
  type Monitor,
  type Alert,
  type Audit,
  type Job,
  type Point,
  type Result,
} from "./api";
import "./styles.css";
import "./workspace.css";
import { CatalogSearch } from "./CatalogSearch";
import { LocationPicker, type Location } from "./LocationPicker";
import { PresenceCharts, equipmentColor } from "./PresenceCharts";
import { FrameDetections } from "./FrameDetections";
import { CameraFrameViewer } from "./CameraFrameViewer";
import { compareWorkByCodeThenDate, workPhase } from "./workStage";
import {
  analysisNotices,
  emptyNotices,
  enqueueNotices,
  advanceNotices,
  dismissNotice,
  noticeText,
  noticePriority,
  type NoticeEvent,
} from "./analysisNotices";
import { InfoButton } from "./InfoButton";
import { equipmentIconUrl } from "./equipmentIcons";
import { AlertEvidence, AlertBreakdown } from "./AlertEvidence";
import { AuditIcon, AuditDetails } from "./AuditDetails";
import { StageDatePicker } from "./StageDatePicker";
import { WorkResourcePreset } from "./WorkResourcePreset";
import { DetectorLab } from "./DetectorLab";
import { DocumentationPage } from "./DocumentationPage";
import "./analytics.css";

const sourceStatusName: Record<string, string> = {
  queued: "Анализ в очереди",
  preparing: "Подготовка источника",
  assessing: "Сопоставление результатов",
  empty: "Нет результатов",
  running: "Идёт анализ",
  reconnecting: "Ожидание повторного подключения",
  connecting: "Подключаемся к потоку",
  connection_queued: "Подключение в очереди",
  ready: "Готов к запуску",
  completed: "Результаты готовы",
  failed: "Ошибка анализа",
  cancelled: "Анализ отменён",
  stopped: "Анализ остановлен",
};
const displayFindingMessage = (
  message: string,
  kind?: string,
  classes?: { class_id: string; count: number }[],
) => {
  if (kind === "excess" || message === "Техника сверх плана") {
    const excess = classes?.reduce((total, item) => total + item.count, 0);
    return excess
      ? `Сверх плана ${excess} ед. техники`
      : /^Сверх плана \d+ ед\. техники$/.test(message)
        ? message
        : "Обнаружена техника сверх плана";
  }
  return message.replace(
    "Количество превышает план; параллельные работы не объясняют избыток",
    "Обнаружена техника сверх плана",
  );
};
const alertKindName = (kind: string) =>
  kind === "missing"
    ? "Отсутствующая техника"
    : kind === "excess"
      ? "Обнаружена техника сверх плана"
      : kind === "idle"
        ? "Простаивание техники"
        : "Отклонение";
const assessmentStatusName: Record<string, string> = {
  ready: "Отклонений не выявлено",
  no_plan: "Нет утверждённого плана",
  no_binding: "Этап не привязан к кадру",
  no_active_work: "Нет активных этапов",
  unknown_time: "Не задано время наблюдения",
  insufficient_evidence: "Недостаточно данных для оценки",
};
const demoDurationParts = (seconds?: number) => {
  const totalMinutes = Math.max(1, Math.round((seconds || 8 * 3600) / 60));
  return {
    hours: Math.floor(totalMinutes / 60),
    minutes: totalMinutes % 60,
  };
};
const demoDurationSeconds = (hours: number, minutes: number) =>
  (hours * 60 + minutes) * 60;

const nav = [
  { id: "overview", label: "Обзор объекта", icon: Layers3 },
  { id: "plan", label: "План работ", icon: CalendarDays },
  { id: "sources", label: "Камеры и видео", icon: Camera },
  { id: "alerts", label: "Отклонения", icon: TriangleAlert },
  { id: "reports", label: "Отчёты", icon: FileSpreadsheet },
  { id: "audit", label: "Журнал событий", icon: History },
  { id: "settings", label: "Настройки", icon: Settings2 },
];
const homeTabs = [
  {
    label: "Все объекты",
    text: "Выбор площадки, поиск и создание объекта.",
    icon: FolderOpen,
  },
  {
    label: "Обзор объекта",
    text: "Выбор этапа по камере, полная история присутствия и активности, сигналы.",
    icon: Layers3,
  },
  {
    label: "План работ",
    text: "Этапы, сроки, требуемая техника и привязка камер или видео.",
    icon: CalendarDays,
  },
  {
    label: "Камеры и видео",
    text: "Мониторинг источников, зоны контроля и повторный анализ.",
    icon: Camera,
  },
  {
    label: "Отклонения",
    text: "Недобор и избыток техники со свидетельствами.",
    icon: TriangleAlert,
  },
  {
    label: "Отчёты",
    text: "Выгрузка результатов и исходных наблюдений.",
    icon: FileSpreadsheet,
  },
  {
    label: "Журнал событий",
    text: "История планов, запусков и действий оператора.",
    icon: History,
  },
  {
    label: "Настройки",
    text: "Параметры модели, простоя, сигналов и классов техники.",
    icon: Settings2,
  },
  {
    label: "Проверка детектора",
    text: "Независимый запуск модели на фото, видео или кадрах.",
    icon: ScanLine,
  },
];
function useData<T>(path: string | null, revision = 0, poll = 0) {
  const [data, setData] = useState<T | null>(null),
    [loadedPath, setLoadedPath] = useState<string | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true);
  useEffect(() => {
    setError("");
    setLoading(true);
    if (!path) {
      setLoading(false);
      return;
    }
    let active = true;
    let inFlight = false;
    let controller: AbortController;
    const load = () => {
      if (inFlight) return;
      inFlight = true;
      controller = new AbortController();
      api<T>(path, { signal: controller.signal })
        .then((d) => {
          if (active) {
            setData(d);
            setLoadedPath(path);
            setError("");
          }
        })
        .catch((e) => {
          if (active && e.name !== "AbortError") setError(e.message);
        })
        .finally(() => {
          inFlight = false;
          if (active) setLoading(false);
        });
    };
    load();
    const timer = poll ? setInterval(load, poll) : null;
    return () => {
      active = false;
      controller?.abort();
      if (timer) clearInterval(timer);
    };
  }, [path, revision, poll]);
  return { data: loadedPath === path ? data : null, error, loading, setData };
}
function Badge({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: string;
}) {
  return (
    <span className={"badge " + tone}>
      <span />
      {children}
    </span>
  );
}
function Empty({
  icon: Icon = FolderOpen,
  title,
  text,
  action,
}: {
  icon?: typeof Camera;
  title: string;
  text: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Icon size={27} />
      </div>
      <h3>{title}</h3>
      <p>{text}</p>
      {action}
    </div>
  );
}
function Modal({
  title,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const old = document.activeElement as HTMLElement;
    ref.current?.focus();
    const fn = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !document.fullscreenElement) close.current();
      if (e.key === "Tab") {
        const nodes = ref.current?.querySelectorAll<HTMLElement>(
          "button,input,select,textarea,a[href]",
        );
        if (!nodes?.length) return;
        const first = nodes[0],
          last = nodes[nodes.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", fn);
    return () => {
      document.removeEventListener("keydown", fn);
      old?.focus();
    };
  }, []);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={ref}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={"modal " + (wide ? "wide" : "")}
      >
        <div className="modal-heading">
          <h2>{title}</h2>
          <button
            className="icon-button"
            aria-label="Закрыть"
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
const inputDate = (v: string) => {
  if (!v || !Number.isFinite(Date.parse(v))) return "";
  const d = new Date(v);
  return new Date(d.getTime() + 3 * 3600e3).toISOString().slice(0, 16);
};
const isoDate = (v: string) => new Date(v + ":00+03:00").toISOString();
const twoDigits = (value: number) => String(value).padStart(2, "0");

function StableDateTimeInput({
  value,
  onChange,
  required = false,
  disabled = false,
}: {
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
  disabled?: boolean;
}) {
  const [datePart = "", timePart = "00:00"] = value.split("T"),
    [hour = "00", minute = "00"] = timePart.split(":");
  const withTime = (nextHour: string, nextMinute: string) =>
    datePart ? `${datePart}T${nextHour}:${nextMinute}` : "";
  return (
    <div
      className="stable-datetime-input"
      role="group"
      aria-label="Дата и время начала записи, МСК"
    >
      <input
        type="date"
        aria-label="Дата начала записи"
        required={required}
        disabled={disabled}
        value={datePart}
        onChange={(event) =>
          onChange(
            event.target.value ? `${event.target.value}T${hour}:${minute}` : "",
          )
        }
      />
      <div
        className="clock-time-group"
        role="group"
        aria-label="Время начала записи"
      >
        <select
          aria-label="Часы начала записи"
          disabled={disabled || !datePart}
          value={hour}
          onChange={(event) => onChange(withTime(event.target.value, minute))}
        >
          {Array.from({ length: 24 }, (_, index) => twoDigits(index)).map(
            (item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ),
          )}
        </select>
        <span aria-hidden="true">:</span>
        <select
          aria-label="Минуты начала записи"
          disabled={disabled || !datePart}
          value={minute}
          onChange={(event) => onChange(withTime(hour, event.target.value))}
        >
          {Array.from({ length: 60 }, (_, index) => twoDigits(index)).map(
            (item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ),
          )}
        </select>
      </div>
    </div>
  );
}

const headerTimeFormat = new Intl.DateTimeFormat("ru-RU", {
  timeZone: "Europe/Moscow",
  hour: "numeric",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

function HeaderClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const update = () => setNow(new Date());
    const timer = window.setInterval(update, 1000);
    document.addEventListener("visibilitychange", update);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", update);
    };
  }, []);
  return (
    <span
      className="timezone header-clock"
      title="Московское время · Europe/Moscow"
    >
      <Clock3 size={16} aria-hidden="true" />
      <span className="clock-values">
        <time dateTime={now.toISOString()}>{headerTimeFormat.format(now)}</time>
        <span className="clock-zone">UTC+3</span>
      </span>
    </span>
  );
}

function App() {
  const initial = new URLSearchParams(location.search);
  const [projectId, setProjectId] = useState(initial.get("project") || ""),
    [page, setPage] = useState(
      initial.get("tab") || (initial.get("project") ? "overview" : "home"),
    ),
    [revision, setRevision] = useState(0),
    [notices, setNotices] = useState(emptyNotices),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [draft, setDraft] = useState<null | "new" | Project>(null),
    [removing, setRemoving] = useState<Project | null>(null),
    [statisticsSourceId, setStatisticsSourceId] = useState("");
  const { data: projects, error: projectsError } = useData<Project[]>(
      page === "docs" ? null : "/projects",
      revision,
      ["home", "projects"].includes(page) ? 5000 : 0,
    ),
    { data: catalog } = useData<Catalog>("/catalog");
  const current = projects?.find((p) => p.id === projectId);
  const inside =
    Boolean(current) &&
    !["home", "projects", "detector", "docs"].includes(page);
  const { data: monitor, error: monitorError } = useData<Monitor>(
    inside ? `/projects/${projectId}/monitoring` : null,
    revision,
    5000,
  );
  const { data: analysisJobs } = useData<Job[]>(
    inside ? `/projects/${projectId}/jobs` : null,
    revision,
    5000,
  );
  const noticeSession = useRef({
    since: Date.now(),
    seen: new Set<string>(),
    initializedProjects: new Set<string>(),
  });
  const nextNoticeId = useRef(0);
  const showNotice = (message: string, type: NoticeEvent["type"] = "saved") => {
    if (!message) return;
    const id = ++nextNoticeId.current;
    setNotices((state) =>
      enqueueNotices(
        state,
        [
          {
            key: `action:${id}`,
            type,
            message,
            projectId: current?.id || "",
            projectName: current?.name || "",
          },
        ],
        Date.now(),
      ),
    );
  };
  useEffect(() => {
    if (!inside || !analysisJobs || !current) return;
    const session = noticeSession.current;
    const firstLoad = !session.initializedProjects.has(current.id);
    const events = analysisNotices(
      analysisJobs,
      session.seen,
      firstLoad ? Number.POSITIVE_INFINITY : session.since,
      current,
    );
    session.initializedProjects.add(current.id);
    if (!firstLoad && events.length)
      setNotices((state) => enqueueNotices(state, events, Date.now()));
  }, [analysisJobs, inside, current]);
  useEffect(() => {
    const deadlines = [...notices.visible, ...notices.pending].flatMap(
      (group) => (group.expiresAt === null ? [] : [group.expiresAt]),
    );
    if (!deadlines.length) return;
    const timer = window.setTimeout(
      () => setNotices((state) => advanceNotices(state, Date.now())),
      Math.max(0, Math.min(...deadlines) - Date.now()),
    );
    return () => window.clearTimeout(timer);
  }, [notices]);
  const refresh = () => setRevision((v) => v + 1);
  useEffect(() => {
    const globalPage = ["home", "projects", "detector", "docs"].includes(page);
    const query = new URLSearchParams({
      tab: page,
      ...(!globalPage && projectId ? { project: projectId } : {}),
    });
    history.replaceState(null, "", page === "home" ? "/" : `?${query}`);
  }, [page, projectId]);
  const run = async (
    fn: () => Promise<unknown>,
    message = "Изменения сохранены",
  ) => {
    setBusy(true);
    setError("");
    try {
      await fn();
      refresh();
      if (message) showNotice(message);
    } catch (e) {
      setError((e as Error).message);
      showNotice((e as Error).message, "error");
      throw e;
    } finally {
      setBusy(false);
    }
  };
  const quietly = (fn: () => Promise<unknown>) => {
    void fn().catch(() => {});
  };
  const openAlerts =
    monitor?.alerts.filter((a) => a.status === "open" && !a.reviewed) || [];
  const openHome = () => {
    setProjectId("");
    setStatisticsSourceId("");
    setPage("home");
  };
  const openProjects = () => {
    setProjectId("");
    setStatisticsSourceId("");
    setPage("projects");
  };
  const openDetector = () => {
    setProjectId("");
    setPage("detector");
  };
  const openDocs = () => {
    setProjectId("");
    setStatisticsSourceId("");
    setPage("docs");
  };
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="/"
          onClick={(e) => {
            e.preventDefault();
            openHome();
          }}
        >
          <div className="brand-mark">
            <img src="/stroydzor-logo.png" alt="" />
          </div>
          <div>
            СтройДозор<small>МОНИТОРИНГ ОБЪЕКТОВ</small>
          </div>
        </a>
        <nav>
          <button
            className={"nav-home" + (page === "home" ? " selected" : "")}
            onClick={openHome}
          >
            <Home size={19} />
            Главная
          </button>
          <button
            className={page === "projects" ? "selected" : ""}
            onClick={openProjects}
          >
            <FolderOpen size={19} />
            Все объекты
          </button>
          <button
            className={page === "detector" ? "selected" : ""}
            onClick={openDetector}
          >
            <ScanLine size={19} />
            Проверка детектора
          </button>
          <button
            className={page === "docs" ? "selected" : ""}
            onClick={openDocs}
          >
            <BookOpen size={19} />
            Документация
          </button>
          {inside && current && (
            <>
              <div className="object-nav-label" title={current.name}>
                {current.name}
              </div>
              {nav.map((item) => (
                <button
                  key={item.id}
                  className={page === item.id ? "selected" : ""}
                  onClick={() => setPage(item.id)}
                >
                  <item.icon size={19} />
                  {item.label}
                  {item.id === "alerts" && openAlerts.length > 0 && (
                    <b>{openAlerts.length}</b>
                  )}
                </button>
              ))}
            </>
          )}
        </nav>
        <div className="user">
          <div className="avatar">ОП</div>
          <div>
            Оператор<small>Демонстрационный режим</small>
          </div>
          <MoreHorizontal size={18} />
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            <a
              href="/"
              onClick={(e) => {
                e.preventDefault();
                openHome();
              }}
            >
              Главная
            </a>
            {page === "projects" && (
              <>
                <span>/</span>
                <strong>Все объекты</strong>
              </>
            )}
            {inside && current && (
              <>
                <span>/</span>
                <a
                  href="?tab=projects"
                  onClick={(e) => {
                    e.preventDefault();
                    openProjects();
                  }}
                >
                  Все объекты
                </a>
                <span>/</span>
                <strong>{current.name}</strong>
              </>
            )}
            {page === "detector" && (
              <>
                <span>/</span>
                <strong>Проверка детектора</strong>
              </>
            )}
            {page === "docs" && (
              <>
                <span>/</span>
                <strong>Документация</strong>
              </>
            )}
          </div>
          <div className="top-actions">
            <HeaderClock />
            <button className="icon-button" title="Обновить" onClick={refresh}>
              <RefreshCw size={18} />
            </button>
            {inside && (
              <button
                className="icon-button"
                aria-label="Отклонения"
                onClick={() => setPage("alerts")}
              >
                <Bell size={18} />
                {openAlerts.length > 0 && <i />}
              </button>
            )}
          </div>
        </header>
        <main>
          {(error || projectsError || monitorError) && (
            <div role="alert" className="banner danger">
              <TriangleAlert size={19} />
              <span>{error || projectsError || monitorError}</span>
              <button
                className="icon-button"
                aria-label="Закрыть ошибку"
                onClick={() => setError("")}
              >
                <X size={16} />
              </button>
            </div>
          )}
          {page === "docs" ? (
            <DocumentationPage onOpenProjects={openProjects} />
          ) : !projects ? (
            <div className="loading">
              <LoaderCircle className="spin" />
              Подключение к серверу…
            </div>
          ) : page === "home" ? (
            <HomePage
              projectCount={projects.length}
              onOpenProjects={openProjects}
              onOpenDetector={openDetector}
              onOpenDocs={openDocs}
            />
          ) : page === "detector" ? (
            <DetectorLab catalog={catalog} />
          ) : page === "projects" || !current ? (
            <ProjectsPage
              catalog={catalog}
              projects={projects}
              onCreate={() => setDraft("new")}
              onEdit={setDraft}
              onRemove={setRemoving}
              onOpen={(id) => {
                setProjectId(id);
                setStatisticsSourceId("");
                setPage("overview");
              }}
            />
          ) : (
            catalog && (
              <React.Fragment key={current.id}>
                {page === "overview" && (
                  <Overview
                    monitor={monitor}
                    project={current}
                    catalog={catalog}
                    navigate={setPage}
                    preferredStatisticsSourceId={statisticsSourceId}
                    run={run}
                    quiet={quietly}
                  />
                )}
                {page === "plan" && (
                  <PlanPage
                    onOpenOverview={() => setPage("overview")}
                    onOpenSources={() => setPage("sources")}
                    project={current}
                    catalog={catalog}
                    monitor={monitor}
                    revision={revision}
                    run={run}
                    quiet={quietly}
                  />
                )}
                {page === "sources" && (
                  <SourcesPage
                    project={current}
                    catalog={catalog}
                    monitor={monitor}
                    revision={revision}
                    onOpenStatistics={(sourceId) => {
                      setStatisticsSourceId(sourceId);
                      setPage("overview");
                    }}
                    run={run}
                    quiet={quietly}
                  />
                )}
                {page === "alerts" && (
                  <AlertsPage
                    catalog={catalog}
                    project={current}
                    alerts={monitor?.alerts || []}
                    run={run}
                    quiet={quietly}
                  />
                )}
                {page === "audit" && (
                  <AuditPage
                    project={current}
                    revision={revision}
                    catalog={catalog}
                  />
                )}
                {page === "reports" && (
                  <ReportsPage project={current} monitor={monitor} />
                )}
                {page === "settings" && (
                  <SettingsPage
                    project={current}
                    catalog={catalog}
                    run={run}
                    quiet={quietly}
                  />
                )}
              </React.Fragment>
            )
          )}
        </main>
      </div>
      {notices.visible.length > 0 && (
        <div className="toast-stack" aria-live="polite">
          {notices.visible.map((notice) => {
            const text = noticeText(notice);
            const failed = noticePriority(notice.type) === 2;
            const first = notice.events[0];
            const singleProject = notice.events.every(
              (event) => event.projectId === first.projectId,
            );
            return (
              <div
                key={notice.id}
                role={failed ? "alert" : "status"}
                className={`toast${failed ? " toast-error" : ""}`}
              >
                {failed ? (
                  <TriangleAlert size={18} />
                ) : notice.type === "analysis_cancelled" ? (
                  <Square size={18} />
                ) : notice.type === "analysis_started" ? (
                  <Clock3 size={18} />
                ) : (
                  <Check size={18} />
                )}
                <div className="toast-copy">
                  <strong>{text.title}</strong>
                  <span title={text.summary}>{text.summary}</span>
                  {first.projectId && (
                    <button
                      className="toast-link"
                      onClick={() => {
                        setProjectId(singleProject ? first.projectId : "");
                        setStatisticsSourceId("");
                        setPage(singleProject ? "overview" : "projects");
                      }}
                    >
                      {singleProject ? "Открыть объект" : "Открыть объекты"}
                      <ArrowUpRight size={13} />
                    </button>
                  )}
                </div>
                <button
                  className="icon-button"
                  aria-label="Закрыть уведомление"
                  title="Закрыть уведомление"
                  onClick={() =>
                    setNotices((state) =>
                      dismissNotice(state, notice.id, Date.now()),
                    )
                  }
                >
                  <X size={16} />
                </button>
              </div>
            );
          })}
        </div>
      )}
      {busy && (
        <div className="busy" role="status">
          <LoaderCircle className="spin" size={17} />
          Сохранение…
        </div>
      )}
      {draft && (
        <ProjectModal
          catalog={catalog}
          project={draft === "new" ? null : draft}
          onClose={() => setDraft(null)}
          onSave={(data) =>
            quietly(() =>
              run(
                async () => {
                  if (draft === "new") {
                    await post<Project>("/projects", {
                      ...data,
                      timezone: "Europe/Moscow",
                    });
                    setDraft(null);
                    setPage("projects");
                  } else {
                    await api(`/projects/${draft.id}`, {
                      method: "PUT",
                      body: JSON.stringify({
                        ...data,
                        expected_revision: draft.revision,
                      }),
                    });
                    setDraft(null);
                  }
                },
                draft === "new" ? "Объект создан" : "Объект обновлён",
              ),
            )
          }
        />
      )}
      {removing && (
        <Modal title="Удалить объект" onClose={() => setRemoving(null)}>
          <p>
            «{removing.name}» исчезнет из списка вместе с планом, камерами и
            журналом этого объекта. Это действие нельзя отменить.
          </p>
          <div className="form-actions">
            <button
              type="button"
              className="button secondary"
              onClick={() => setRemoving(null)}
            >
              Отмена
            </button>
            <button
              className="button destructive"
              onClick={() =>
                quietly(() =>
                  run(async () => {
                    await api(`/projects/${removing.id}`, {
                      method: "DELETE",
                      body: JSON.stringify({
                        expected_revision: removing.revision,
                      }),
                    });
                    if (projectId === removing.id) {
                      setProjectId("");
                      setPage("projects");
                    }
                    setRemoving(null);
                  }, "Объект удалён"),
                )
              }
            >
              <Trash2 size={16} />
              Удалить объект
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
type Run = (fn: () => Promise<unknown>, message?: string) => Promise<void>;
type Quiet = (fn: () => Promise<unknown>) => void;
function tileStage(project: Project) {
  const works = project.summary?.current_works || [];
  if (works.length) {
    const extra = works.length > 1 ? ` · ещё ${works.length - 1}` : "";
    return `Идёт: ${works[0].code} ${works[0].title}${extra}`;
  }
  return project.current_plan_id ? "Сейчас по графику пауза" : "";
}
function tileCameras(project: Project) {
  const total = project.summary?.sources ?? 0;
  const live = project.summary?.live_sources ?? 0;
  return total ? `Камеры: ${total} · в эфире ${live}` : "Нет камер";
}
function alertLabel(count: number) {
  const ten = count % 10,
    hundred = count % 100;
  if (ten === 1 && hundred !== 11) return `${count} отклонение`;
  if (ten >= 2 && ten <= 4 && (hundred < 12 || hundred > 14))
    return `${count} отклонения`;
  return `${count} отклонений`;
}
function HomePage({
  projectCount,
  onOpenProjects,
  onOpenDetector,
  onOpenDocs,
}: {
  projectCount: number;
  onOpenProjects: () => void;
  onOpenDetector: () => void;
  onOpenDocs: () => void;
}) {
  return (
    <div className="home-page">
      <section className="home-hero">
        <div className="home-hero-copy">
          <div className="eyebrow">СТРОЙДОЗОР</div>
          <h1>Техника на площадке. План под контролем.</h1>
          <p>
            Создайте план, привяжите к этапам камеры или видео и наблюдайте
            присутствие и активность техники во времени. Система показывает
            проверяемые сигналы отклонений и кадры, на которых они основаны.
          </p>
          <div className="home-actions">
            <button className="button primary" onClick={onOpenProjects}>
              <FolderOpen size={18} />
              Выбрать объект
            </button>
            <button className="button secondary" onClick={onOpenDetector}>
              <ScanLine size={18} />
              Проверить детектор
            </button>
            <a
              className="button secondary"
              href="?tab=docs"
              onClick={(event) => {
                event.preventDefault();
                onOpenDocs();
              }}
            >
              <BookOpen size={18} />
              Документация
            </a>
          </div>
        </div>
        <div className="home-route" aria-label="Как работает приложение">
          <span>
            <CalendarDays size={19} /> План и техника
          </span>
          <ArrowRight size={18} aria-hidden="true" />
          <span>
            <Camera size={19} /> Источники и зоны
          </span>
          <ArrowRight size={18} aria-hidden="true" />
          <span>
            <ShieldCheck size={19} /> Графики и сигналы
          </span>
        </div>
      </section>

      <section className="home-starts" aria-label="Основные действия">
        <button className="home-start-card objects" onClick={onOpenProjects}>
          <span className="home-start-icon">
            <FolderOpen size={28} />
          </span>
          <span>
            <strong>Объекты</strong>
            <small>
              {projectCount
                ? `Открыть одну из ${projectCount} площадок или добавить новую.`
                : "Добавить первую площадку и её план."}
            </small>
          </span>
          <ArrowUpRight size={20} />
        </button>
        <button className="home-start-card detector" onClick={onOpenDetector}>
          <span className="home-start-icon">
            <ScanLine size={28} />
          </span>
          <span>
            <strong>Проверка детектора</strong>
            <small>Запустить модель отдельно от объектов и аналитики.</small>
          </span>
          <ArrowUpRight size={20} />
        </button>
      </section>

      <section className="home-sections">
        <div className="home-section-heading">
          <div>
            <div className="eyebrow">РАЗДЕЛЫ</div>
            <h2>Что доступно в приложении</h2>
          </div>
          <p>Вкладки объекта появляются после выбора площадки.</p>
        </div>
        <div className="home-section-grid">
          {homeTabs.map((item) => (
            <article key={item.label}>
              <span>
                <item.icon size={19} />
              </span>
              <div>
                <h3>{item.label}</h3>
                <p>{item.text}</p>
              </div>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}

function ProjectTypeIcon({
  typeId,
  size = 26,
}: {
  typeId: string | null;
  size?: number;
}) {
  const props = { size, strokeWidth: 1.9, "aria-hidden": true };
  switch (typeId) {
    case "housing":
      return <Home {...props} />;
    case "education":
      return <GraduationCap {...props} />;
    case "healthcare":
      return <Hospital {...props} />;
    case "sport":
      return <Trophy {...props} />;
    case "culture":
      return <Drama {...props} />;
    case "administration":
      return <Landmark {...props} />;
    case "preschool":
      return <Baby {...props} />;
    case "office":
      return <BriefcaseBusiness {...props} />;
    case "roads":
      return <Route {...props} />;
    default:
      return <Building2 {...props} />;
  }
}

function projectTypeTone(typeId: string | null) {
  return typeId || "unspecified";
}

function ProjectsPage({
  catalog,
  projects,
  onCreate,
  onEdit,
  onRemove,
  onOpen,
}: {
  catalog: Catalog | null;
  projects: Project[];
  onCreate: () => void;
  onEdit: (project: Project) => void;
  onRemove: (project: Project) => void;
  onOpen: (id: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const [planFilter, setPlanFilter] = useState("all");
  const [sortOrder, setSortOrder] = useState<"date" | "name">("date");
  const words = query
    .toLocaleLowerCase("ru")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  const filtered = projects
    .filter(
      (p) =>
        (typeFilter === "all" ||
          (p.project_type_id || "unspecified") === typeFilter) &&
        (planFilter === "all" ||
          (planFilter === "approved"
            ? Boolean(p.current_plan_id)
            : !p.current_plan_id)) &&
        words.every((w) =>
          `${p.name} ${p.address}`.toLocaleLowerCase("ru").includes(w),
        ),
    )
    .sort((a, b) =>
      sortOrder === "name"
        ? a.name.localeCompare(b.name, "ru") ||
          Date.parse(b.created_at) - Date.parse(a.created_at)
        : Date.parse(b.created_at) - Date.parse(a.created_at) ||
          a.name.localeCompare(b.name, "ru"),
    );
  return (
    <>
      <PageHead
        eyebrow="ОБЪЕКТЫ"
        title="Все объекты"
        description="Сначала площадки. Откройте объект - слева появятся план, камеры и отклонения."
      >
        <button className="button primary" onClick={onCreate}>
          <Plus size={18} />
          Добавить объект
        </button>
      </PageHead>
      {projects.length > 0 && (
        <div className="projects-toolbar">
          <label>
            <Search size={18} />
            <input
              type="search"
              aria-label="Поиск объектов"
              placeholder="Найти объект по названию или адресу"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <div className="projects-filters">
            <select
              aria-label="Фильтр по виду объекта"
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
            >
              <option value="all">Все виды объектов</option>
              <option value="unspecified">Вид не указан</option>
              {catalog?.project_types.map((type) => (
                <option key={type.id} value={type.id}>
                  {type.name}
                </option>
              ))}
            </select>
            <select
              aria-label="Фильтр по статусу плана"
              value={planFilter}
              onChange={(e) => setPlanFilter(e.target.value)}
            >
              <option value="all">Все статусы плана</option>
              <option value="approved">План утверждён</option>
              <option value="none">Плана нет</option>
            </select>
            <select
              aria-label="Сортировка объектов"
              value={sortOrder}
              onChange={(event) =>
                setSortOrder(event.target.value as "date" | "name")
              }
            >
              <option value="date">Сначала новые</option>
              <option value="name">По алфавиту</option>
            </select>
          </div>
          <span>Объектов: {filtered.length}</span>
        </div>
      )}
      {filtered.length ? (
        <div className="projects-grid">
          {filtered.map((p) => {
            const stage = tileStage(p);
            return (
              <article className="project-tile panel" key={p.id}>
                <div className="project-tile-menu">
                  <button
                    type="button"
                    className="icon-button"
                    aria-label="Изменить объект"
                    title="Изменить объект"
                    onClick={() => onEdit(p)}
                  >
                    <Pencil size={16} />
                  </button>
                  <button
                    type="button"
                    className="icon-button"
                    aria-label="Удалить объект"
                    title="Удалить объект"
                    onClick={() => onRemove(p)}
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
                <button
                  type="button"
                  className="project-tile-open"
                  onClick={() => onOpen(p.id)}
                >
                  <div className="project-tile-top">
                    <span
                      className={`project-tile-icon project-type-${projectTypeTone(p.project_type_id)}`}
                    >
                      <ProjectTypeIcon typeId={p.project_type_id} />
                    </span>
                  </div>
                  <h2>{p.name}</h2>
                  <span className="project-type-label">
                    {catalog?.project_types.find(
                      (t) => t.id === p.project_type_id,
                    )?.name || "Вид не указан"}
                  </span>
                  <p>
                    <MapPin size={15} />
                    {p.address || "Адрес не указан"}
                  </p>
                  <div className="project-tile-meta">
                    {stage ? <span>{stage}</span> : null}
                    <span>{tileCameras(p)}</span>
                    <span>Создан {date(p.created_at)}</span>
                  </div>
                  <div className="project-tile-footer">
                    <span className="project-tile-badges">
                      <Badge tone={p.current_plan_id ? "green" : "neutral"}>
                        {p.current_plan_id ? "План утверждён" : "Нет плана"}
                      </Badge>
                      {(p.summary?.open_alerts || 0) > 0 && (
                        <Badge tone="red">
                          {alertLabel(p.summary?.open_alerts || 0)}
                        </Badge>
                      )}
                    </span>
                    <span>Открыть объект</span>
                  </div>
                </button>
              </article>
            );
          })}
        </div>
      ) : (
        <section className="panel projects-empty">
          <Empty
            icon={Building2}
            title={projects.length ? "Объекты не найдены" : "Объектов пока нет"}
            text={
              projects.length ? "Измените поисковый запрос." : "Добавить новый?"
            }
            action={
              !projects.length && (
                <button className="button primary" onClick={onCreate}>
                  <Plus size={17} />
                  Добавить объект
                </button>
              )
            }
          />
        </section>
      )}
    </>
  );
}
function ProjectModal({
  catalog,
  project,
  onClose,
  onSave,
}: {
  catalog: Catalog | null;
  project: Project | null;
  onClose: () => void;
  onSave: (data: {
    name: string;
    address: string;
    project_type_id: string | null;
    latitude: number | null;
    longitude: number | null;
  }) => void;
}) {
  const [name, setName] = useState(project?.name || ""),
    [address, setAddress] = useState(project?.address || "");
  const [projectType, setProjectType] = useState(
    project?.project_type_id || "",
  );
  const [location, setLocation] = useState<Location>(
    project?.latitude != null && project?.longitude != null
      ? { latitude: project.latitude, longitude: project.longitude }
      : null,
  );
  const [showMap, setShowMap] = useState(false);
  const [addressLookupStatus, setAddressLookupStatus] = useState("");
  const [addressSuggestions, setAddressSuggestions] = useState<
    { address: string | null; latitude: number; longitude: number }[]
  >([]);
  const addressRevision = useRef(0),
    lookupRevision = useRef(0);
  useEffect(() => {
    const revision = addressRevision.current;
    const query = address.trim();
    if (!revision || query.length < 3) {
      setAddressLookupStatus("");
      setAddressSuggestions([]);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setAddressLookupStatus("Ищем адрес на карте…");
      api<{
        latitude: number | null;
        longitude: number | null;
        address: string | null;
        suggestions?: {
          address: string | null;
          latitude: number;
          longitude: number;
        }[];
      }>(`/map/search?address=${encodeURIComponent(query)}`, {
        signal: controller.signal,
      })
        .then((result) => {
          if (controller.signal.aborted || revision !== addressRevision.current)
            return;
          if (result.latitude == null || result.longitude == null) {
            setAddressLookupStatus("Адрес не найден на карте");
            setAddressSuggestions([]);
            return;
          }
          setAddressSuggestions(result.suggestions || []);
          setLocation({
            latitude: result.latitude,
            longitude: result.longitude,
          });
          setShowMap(true);
          setAddressLookupStatus(
            "Выберите адрес из подсказок или уточните точку на карте",
          );
        })
        .catch((error) => {
          if (!controller.signal.aborted)
            setAddressLookupStatus(
              error instanceof Error ? error.message : "Не удалось найти адрес",
            );
          setAddressSuggestions([]);
        });
    }, 700);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [address]);
  return (
    <Modal
      title={project ? "Изменить объект" : "Новый объект"}
      onClose={onClose}
    >
      <form
        className="project-form"
        onSubmit={(e) => {
          e.preventDefault();
          onSave({
            name: name.trim(),
            address: address.trim(),
            project_type_id: projectType || null,
            latitude: location?.latitude ?? null,
            longitude: location?.longitude ?? null,
          });
        }}
      >
        <label>
          Вид объекта
          <select
            required={!project}
            aria-label="Вид объекта"
            value={projectType}
            onChange={(e) => setProjectType(e.target.value)}
          >
            <option value="">Выберите вид объекта</option>
            {catalog?.project_types.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Название объекта
          <input
            required
            pattern=".*\S.*"
            title="Укажите название объекта"
            maxLength={200}
            placeholder="ЖК Северный · корпус 2"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label>
          Адрес или описание
          <input
            maxLength={500}
            value={address}
            onChange={(e) => {
              addressRevision.current++;
              setAddress(e.target.value);
              setAddressSuggestions([]);
            }}
            placeholder="Москва, строительная площадка"
          />
        </label>
        {addressSuggestions.length > 0 && (
          <div
            className="address-suggestions"
            role="listbox"
            aria-label="Подсказки адресов"
          >
            {addressSuggestions.map((suggestion, index) => (
              <button
                type="button"
                role="option"
                aria-selected={false}
                key={`${suggestion.latitude}-${suggestion.longitude}-${index}`}
                onClick={() => {
                  addressRevision.current++;
                  setAddress(suggestion.address || address);
                  setLocation({
                    latitude: suggestion.latitude,
                    longitude: suggestion.longitude,
                  });
                  setShowMap(true);
                  setAddressSuggestions([]);
                  setAddressLookupStatus("Адрес и точка выбраны");
                }}
              >
                <MapPin size={15} />{" "}
                {suggestion.address || `Точка ${index + 1}`}
              </button>
            ))}
          </div>
        )}
        <div className="map-choice">
          <button
            type="button"
            className="button secondary"
            onClick={() => setShowMap((v) => !v)}
          >
            <MapPin size={17} />
            {showMap
              ? "Скрыть карту"
              : location
                ? "Изменить точку на карте"
                : "Указать точку на карте"}
          </button>
          {addressLookupStatus && (
            <span className="hint" role="status">
              {addressLookupStatus}
            </span>
          )}
        </div>
        {showMap && (
          <LocationPicker
            value={location}
            onChange={(point) => {
              addressRevision.current++;
              lookupRevision.current = addressRevision.current;
              setAddressLookupStatus("");
              setLocation(point);
            }}
            onAddress={(text) => {
              if (lookupRevision.current === addressRevision.current) {
                addressRevision.current = 0;
                setAddress(text);
              }
            }}
          />
        )}
        <p className="hint">
          Для сохранения заполните название и вид объекта. Адрес и точка
          необязательны.
        </p>
        <div className="form-actions">
          <button type="button" className="button secondary" onClick={onClose}>
            Отмена
          </button>
          <button className="button primary">
            {project ? "Сохранить" : "Добавить объект"}
            <ArrowRight size={16} />
          </button>
        </div>
      </form>
    </Modal>
  );
}
function PageHead({
  title,
  description,
  children,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      <div className="page-actions">{children}</div>
    </div>
  );
}
function AlertReviewModal({
  alert,
  catalog,
  onClose,
  run,
  quiet,
}: {
  alert: Alert;
  catalog: Catalog;
  onClose: () => void;
  run: Run;
  quiet: Quiet;
}) {
  const [note, setNote] = useState("");
  const [reviewError, setReviewError] = useState("");
  const [reviewBusy, setReviewBusy] = useState(false);
  return (
    <Modal
      title={
        alert.kind === "missing"
          ? "Отсутствующая техника"
          : alert.kind === "excess"
            ? "Обнаружена техника сверх плана"
            : "Простаивание техники"
      }
      wide
      onClose={onClose}
    >
      <div className="evidence-layout">
        <AlertEvidence key={alert.id} alert={alert} catalog={catalog} />
        <div>
          <Badge tone={alert.severity === "critical" ? "red" : "amber"}>
            {alert.severity === "critical" ? "Критический" : "Средний"}
          </Badge>
          <h3>{alert.details.title}</h3>
          <p>
            {displayFindingMessage(
              alert.details.message,
              alert.kind,
              alert.details.classes,
            )}
          </p>
          <AlertBreakdown alert={alert} catalog={catalog} />
          <p className="hint">
            {date(alert.first_seen)} - {date(alert.last_seen)}
          </p>
          <p>
            Длительность наблюдаемого условия:{" "}
            {duration(alert.duration_seconds)}
          </p>
          <label>
            Комментарий
            <textarea
              value={note}
              onChange={(event) => setNote(event.target.value)}
              rows={3}
            />
          </label>
          <div className="form-actions">
            {[
              ["dismissed", "Ложный сигнал"],
              ["confirmed", "Подтвердить"],
            ].map(([decision, label]) => (
              <button
                key={decision}
                disabled={reviewBusy}
                className={
                  "button " +
                  (decision === "confirmed" ? "primary" : "secondary")
                }
                onClick={() =>
                  quiet(async () => {
                    setReviewBusy(true);
                    setReviewError("");
                    try {
                      await run(async () => {
                        await post(`/alerts/${alert.id}/reviews`, {
                          decision,
                          note,
                        });
                        onClose();
                      }, "Проверка сохранена в журнале");
                    } catch (error) {
                      setReviewError((error as Error).message);
                    } finally {
                      setReviewBusy(false);
                    }
                  })
                }
              >
                {label}
              </button>
            ))}
          </div>
          {reviewError && (
            <p role="alert" className="banner danger">
              {reviewError}
            </p>
          )}
        </div>
      </div>
    </Modal>
  );
}
function Overview({
  monitor,
  project,
  catalog,
  navigate,
  preferredStatisticsSourceId,
  run,
  quiet,
}: {
  monitor: Monitor | null;
  project: Project;
  catalog: Catalog;
  navigate: (p: string) => void;
  preferredStatisticsSourceId?: string;
  run: Run;
  quiet: Quiet;
}) {
  const cards = monitor?.cards || [],
    works = monitor?.plan?.works || [],
    allAlerts = monitor?.alerts || [],
    alerts = allAlerts.filter((a) => a.status === "open" && !a.reviewed),
    reviewedAlerts = allAlerts.filter((a) => a.reviewed),
    hasDemoObservations = cards.some(
      (card) => card.observation?.model.demo === true,
    );
  const generatedAt = Date.parse(monitor?.generated_at || "");
  const now = Number.isFinite(generatedAt) ? generatedAt : Date.now();
  const phaseByWorkId = new Map(
    works.map((work) => [
      work.id,
      workPhase(work, monitor?.work_states[work.id], now),
    ]),
  );
  const active = works.filter(
    (work) => phaseByWorkId.get(work.id) === "active",
  );
  const completed = works.filter(
    (work) => phaseByWorkId.get(work.id) === "completed",
  );
  const overdue = works.filter(
    (work) =>
      phaseByWorkId.get(work.id) !== "completed" &&
      Date.parse(work.ends_at) <= now,
  );
  const upcoming = works.filter(
    (work) => phaseByWorkId.get(work.id) === "upcoming",
  );
  const completedWorkIds = new Set(completed.map((work) => work.id));
  const currentAlerts = alerts.filter(
    (alert) => !completedWorkIds.has(alert.work_id),
  );
  const completedAlerts = alerts.filter((alert) =>
    completedWorkIds.has(alert.work_id),
  );
  const sourcesWithFrames = cards.filter((card) => card.observation).length;
  const boundSources = cards.filter((card) =>
    card.binding?.regions.some((region) => region.work_ids.length),
  ).length;
  const activeSourceCards = cards.filter((card) =>
    card.binding?.regions.some((region) =>
      region.work_ids.some((id) => phaseByWorkId.get(id) === "active"),
    ),
  );
  const inactiveSourceCards = cards.filter(
    (card) => !activeSourceCards.includes(card),
  );
  const [showInactiveSources, setShowInactiveSources] = useState(false);
  const sourceGroups = [
    { key: "active", title: "Действующие этапы", cards: activeSourceCards },
    ...(showInactiveSources
      ? [
          {
            key: "other",
            title: "Остальные источники",
            cards: inactiveSourceCards,
          },
        ]
      : []),
  ].filter((group) => group.cards.length > 0);
  const visibleCards = sourceGroups.flatMap((group) => group.cards);
  const [attentionTab, setAttentionTab] = useState<"pending" | "reviewed">(
    "pending",
  );
  const [selectedAlert, setSelectedAlert] = useState<Alert | null>(null),
    [frameCard, setFrameCard] = useState<Monitor["cards"][number] | null>(null),
    [statisticsSourceId, setStatisticsSourceId] = useState(
      preferredStatisticsSourceId || "",
    ),
    [statisticsWorkId, setStatisticsWorkId] = useState("");
  useEffect(() => {
    if (preferredStatisticsSourceId)
      setStatisticsSourceId(preferredStatisticsSourceId);
  }, [preferredStatisticsSourceId]);
  const selectStageStatistics = (
    sourceId: string,
    workId: string,
    scroll = false,
  ) => {
    setStatisticsSourceId(sourceId);
    setStatisticsWorkId(workId);
    if (scroll)
      window.setTimeout(
        () =>
          document
            .getElementById("project-statistics")
            ?.scrollIntoView({ behavior: "smooth", block: "start" }),
        0,
      );
  };
  const alertForFinding = (
    sourceId: string,
    observationId: string | undefined,
    workId: string | undefined,
    kind: string,
  ) => {
    const candidates = allAlerts.filter(
      (alert) =>
        alert.source_id === sourceId &&
        alert.work_id === workId &&
        alert.kind === kind,
    );
    return (
      candidates.find((alert) => alert.details.evidence_id === observationId) ||
      candidates.find((alert) => alert.status === "open") ||
      candidates[0]
    );
  };
  return (
    <>
      <PageHead
        eyebrow="СВОДКА НАБЛЮДЕНИЙ"
        title="Обзор объекта"
        description={
          project.address ||
          "Техника, работы и отклонения в одном пространстве."
        }
      >
        <button
          className="button secondary"
          onClick={() => navigate("reports")}
        >
          <ArrowDownToLine size={17} />
          Отчёт
        </button>
        <button className="button primary" onClick={() => navigate("sources")}>
          <Plus size={17} />
          Добавить источник
        </button>
      </PageHead>
      {!monitor?.plan && (
        <section className="panel overview-plan-prompt">
          <div className="overview-plan-symbol">
            <CalendarDays size={28} />
          </div>
          <div>
            <span className="eyebrow">ПЕРВЫЙ ШАГ</span>
            <h2>Добавьте план работ</h2>
            <p>
              Укажите этапы и технику, чтобы сопоставлять наблюдения с графиком
              строительства.
            </p>
          </div>
          <button className="button primary" onClick={() => navigate("plan")}>
            <Plus size={17} /> Создать план
          </button>
        </section>
      )}
      <div className="metric-grid">
        {[
          {
            label: "Этапы в работе",
            value: active.length,
            icon: Activity,
            note: monitor?.plan
              ? "Завершено: " +
                completed.length +
                " из " +
                works.length +
                (upcoming.length ? " · впереди: " + upcoming.length : "")
              : "План ещё не утверждён",
          },
          {
            label: "Сроки требуют внимания",
            value: overdue.length,
            icon: CalendarDays,
            note: monitor?.plan
              ? overdue.length
                ? "Дата окончания прошла"
                : "Просроченных этапов нет"
              : "Сроки появятся после утверждения плана",
          },
          {
            label: "Камеры и видео",
            value: cards.length,
            icon: Camera,
            note:
              "С кадрами: " + sourcesWithFrames + " · к плану: " + boundSources,
          },
          {
            label: "Открытые сигналы",
            value: alerts.length,
            icon: TriangleAlert,
            note: alerts.length
              ? "Текущих: " +
                currentAlerts.length +
                " · по завершённым: " +
                completedAlerts.length
              : "Открытых сигналов нет",
          },
        ].map((m, i) => (
          <div
            className={
              "metric " +
              ((i === 1 && overdue.length) || (i === 3 && alerts.length)
                ? "attention"
                : "")
            }
            key={m.label}
          >
            <div>
              {m.label}
              <m.icon size={19} />
            </div>
            <strong>{m.value}</strong>
            <small>{m.note}</small>
          </div>
        ))}
      </div>
      {hasDemoObservations && (
        <div className="banner model-banner">
          <div className="banner-icon">
            <ScanLine size={22} />
          </div>
          <div>
            <strong>Тестовый объект · случайная заглушка</strong>
            <p>
              Числа, классы и рамки созданы генератором DEMO_RANDOM_STUB. Это
              данные для проверки интерфейса, а не результат распознавания.
            </p>
          </div>
          <Badge tone="amber">ДЕМО</Badge>
        </div>
      )}
      {!hasDemoObservations && !monitor?.model.ready && (
        <div className="banner model-banner">
          <div className="banner-icon">
            <ScanLine size={22} />
          </div>
          <div>
            <strong>Детектор ещё не подключён</strong>
            <p>
              План, камеры и зоны доступны. Результаты распознавания появятся
              после подключения модели.
            </p>
          </div>
          <Badge tone="amber">Ожидает модель</Badge>
        </div>
      )}
      <div className="dashboard-columns">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>Наблюдение за площадкой</h2>
              <span>Последние кадры и состояние источников</span>
            </div>
            <div className="panel-heading-actions">
              <button
                className="text-button"
                onClick={() => navigate("sources")}
              >
                Все источники
                <ArrowUpRight size={16} />
              </button>
              <InfoButton title="Наблюдение за площадкой">
                <p>
                  Выбор этапа обновляет статистику ниже. Кнопка «Результаты и
                  статистика» прокручивает к ней, кнопка в углу кадра открывает
                  полноэкранный просмотр.
                </p>
              </InfoButton>
            </div>
          </div>
          {cards.length > activeSourceCards.length && (
            <div className="camera-source-toolbar">
              <button
                type="button"
                className="text-button"
                onClick={() => setShowInactiveSources((value) => !value)}
              >
                {showInactiveSources
                  ? "Скрыть источники без действующих этапов"
                  : `Показать остальные источники (${cards.length - activeSourceCards.length})`}
              </button>
            </div>
          )}
          {!visibleCards.length ? (
            <Empty
              icon={Camera}
              title={
                cards.length
                  ? "Нет источников действующих этапов"
                  : "Добавьте взгляд на площадку"
              }
              text={
                cards.length
                  ? "Остальные источники доступны по кнопке выше."
                  : "Загрузите видео, снимок или подключите RTSP/HTTPS-поток. Затем выделите зоны работ."
              }
              action={
                <button
                  className="button secondary"
                  onClick={() => navigate("sources")}
                >
                  <Plus size={16} />
                  Добавить источник
                </button>
              }
            />
          ) : (
            <div className="camera-source-sections">
              {sourceGroups.map((group) => (
                <section className="camera-source-section" key={group.key}>
                  <h3 className="camera-source-section-title">{group.title}</h3>
                  <div className="camera-grid">
                    {group.cards.map((card) => {
                      const boundWorks = works
                        .filter((work) =>
                          card.binding?.regions.some((region) =>
                            region.work_ids.includes(work.id),
                          ),
                        )
                        .sort(compareWorkByCodeThenDate);
                      const liveWorks = boundWorks.filter(
                        (work) => phaseByWorkId.get(work.id) === "active",
                      );
                      const completedWorks = boundWorks.filter(
                        (work) => phaseByWorkId.get(work.id) === "completed",
                      );
                      const upcomingWorks = boundWorks.filter(
                        (work) => phaseByWorkId.get(work.id) === "upcoming",
                      );
                      const defaultWork = liveWorks[0];
                      const cardWork =
                        boundWorks.find(
                          (work) =>
                            statisticsSourceId === card.source.id &&
                            statisticsWorkId === work.id,
                        ) ||
                        defaultWork ||
                        boundWorks[0];
                      const liveWorkIds = new Set(
                        liveWorks.map((work) => work.id),
                      );
                      const activeResults =
                        card.assessment?.results.filter(
                          (result) =>
                            result.work_id && liveWorkIds.has(result.work_id),
                        ) || [];
                      const renderResult = (result: Result, index: number) => (
                        <div
                          className="camera-result"
                          key={result.work_id || index}
                        >
                          <span
                            className={
                              result.findings?.length
                                ? "camera-assessment-warning"
                                : ""
                            }
                          >
                            {result.findings?.length
                              ? result.findings.some(
                                  (finding) => finding.kind === "missing",
                                )
                                ? "Нужна проверка состава техники"
                                : result.findings.some(
                                      (finding) => finding.kind === "idle",
                                    )
                                  ? "Зафиксирован простой"
                                  : "Обнаружена техника сверх плана"
                              : assessmentStatusName[result.readiness] ||
                                stateName[result.readiness] ||
                                "Наблюдения доступны"}
                          </span>
                          {result.findings?.map((finding, findingIndex) => {
                            const relatedAlert = alertForFinding(
                              card.source.id,
                              card.observation?.id,
                              result.work_id,
                              finding.kind,
                            );
                            return relatedAlert ? (
                              <button
                                type="button"
                                className={`camera-finding-link ${relatedAlert.severity === "critical" ? "critical" : "warning"}`}
                                key={findingIndex}
                                onClick={() => setSelectedAlert(relatedAlert)}
                              >
                                <span>
                                  {displayFindingMessage(
                                    finding.message,
                                    finding.kind,
                                    finding.classes,
                                  )}
                                </span>
                                <ArrowUpRight size={14} />
                              </button>
                            ) : (
                              <small
                                className="camera-finding-note"
                                key={findingIndex}
                              >
                                {displayFindingMessage(
                                  finding.message,
                                  finding.kind,
                                  finding.classes,
                                )}
                              </small>
                            );
                          })}
                        </div>
                      );
                      const stageItem = (work: Work) => (
                        <header key={work.id} className="camera-stage-item">
                          <span className="camera-stage-title">
                            {work.title}
                          </span>
                          <small>{work.code}</small>
                        </header>
                      );
                      return (
                        <div
                          className={
                            "camera-card" +
                            (cardWork ? " selectable" : "") +
                            (statisticsSourceId === card.source.id &&
                            boundWorks.some(
                              (work) => work.id === statisticsWorkId,
                            )
                              ? " selected"
                              : "")
                          }
                          key={card.source.id}
                          onClick={(event) => {
                            if (
                              !cardWork ||
                              (event.target as Element).closest(
                                "button, a, summary, input, select, textarea",
                              )
                            )
                              return;
                            selectStageStatistics(card.source.id, cardWork.id);
                          }}
                        >
                          <div className="camera-stage-list">
                            {liveWorks.length > 0 && (
                              <div className="camera-stage-group">
                                <div className="camera-stage-items">
                                  {liveWorks.map(stageItem)}
                                </div>
                              </div>
                            )}
                            {completedWorks.length > 0 && (
                              <details className="camera-stage-group">
                                <summary>
                                  Завершённые этапы · {completedWorks.length}
                                </summary>
                                <div className="camera-stage-items">
                                  {completedWorks.map(stageItem)}
                                </div>
                              </details>
                            )}
                            {!liveWorks.length && (
                              <details className="camera-stage-group">
                                <summary>Нет действующего этапа</summary>
                                {upcomingWorks.length ? (
                                  <div className="camera-stage-items">
                                    {upcomingWorks.map(stageItem)}
                                  </div>
                                ) : (
                                  <p className="camera-stage-empty">
                                    У этого источника сейчас нет действующих
                                    этапов.
                                  </p>
                                )}
                              </details>
                            )}
                          </div>
                          <div className="camera-image-wrap">
                            <button
                              type="button"
                              aria-label={
                                "Показать статистику источника " +
                                card.source.name
                              }
                              onClick={() =>
                                selectStageStatistics(
                                  card.source.id,
                                  cardWork?.id || "",
                                )
                              }
                              className="camera-image camera-image-button"
                              style={{
                                aspectRatio: `${card.source.metadata_json.width || 16} / ${card.source.metadata_json.height || 9}`,
                              }}
                            >
                              {card.observation || card.source.preview_url ? (
                                <img
                                  src={
                                    card.observation
                                      ? "/api/v1/evidence/" +
                                        card.observation.id
                                      : card.source.preview_url || ""
                                  }
                                  alt={card.source.name}
                                />
                              ) : (
                                <Camera size={32} />
                              )}
                              <span className="camera-kind">
                                {card.source.kind === "rtsp"
                                  ? "ПОТОК"
                                  : card.source.kind === "video"
                                    ? "ВИДЕО"
                                    : "СНИМОК"}
                              </span>
                              {card.observation && (
                                <Boxes
                                  detections={card.observation.detections.filter(
                                    (detection) => detection.observed !== false,
                                  )}
                                  catalog={catalog}
                                />
                              )}
                            </button>
                            {(card.observation || card.source.preview_url) && (
                              <button
                                type="button"
                                className="camera-expand-button"
                                aria-label={
                                  "Открыть кадр источника " +
                                  card.source.name +
                                  " на весь экран"
                                }
                                onClick={() => setFrameCard(card)}
                              >
                                <Maximize2 size={19} />
                              </button>
                            )}
                          </div>
                          <div className="camera-info">
                            <strong>{card.source.name}</strong>
                            <Badge
                              tone={
                                card.source.status === "failed"
                                  ? "red"
                                  : "neutral"
                              }
                            >
                              {sourceStatusText(card.source)}
                            </Badge>
                          </div>
                          <small className="camera-time">
                            {card.observation
                              ? date(card.observation.captured_at)
                              : "Наблюдения ещё не получены"}
                          </small>
                          {card.source.status === "failed" &&
                            card.source.error && (
                              <div className="inline-warning" role="alert">
                                {card.source.error}
                              </div>
                            )}
                          {card.stale_context && (
                            <div className="inline-warning">
                              Условия оценки изменились после анализа. Повторите
                              анализ для обновления результата.
                            </div>
                          )}
                          {card.stale_plan && (
                            <div className="inline-warning">
                              Оценка по предыдущей версии плана
                            </div>
                          )}
                          {activeResults.length > 0 && (
                            <h4 className="camera-results-heading">
                              Результаты по этапу
                            </h4>
                          )}
                          {activeResults.map(renderResult)}
                          {card.source.status === "completed" && (
                            <button
                              type="button"
                              className="camera-statistics-link"
                              onClick={() =>
                                selectStageStatistics(
                                  card.source.id,
                                  cardWork?.id || "",
                                  true,
                                )
                              }
                            >
                              <Activity size={15} />
                              Результаты и статистика
                              <ArrowDownToLine size={15} />
                            </button>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </section>
              ))}
            </div>
          )}
        </section>
        {frameCard && (
          <CameraFrameViewer
            card={frameCard}
            catalog={catalog}
            onClose={() => setFrameCard(null)}
          />
        )}
        <div id="overview-stage-summary" />
        <section className="panel activity-panel">
          <div className="panel-heading">
            <div>
              <h2>Требует внимания</h2>
            </div>
            <div className="panel-heading-actions">
              <span className="count-chip">{alerts.length}</span>
              <InfoButton title="Требует внимания">
                <p>
                  Сначала показаны сигналы незавершённых этапов. Открытые
                  сигналы завершённых этапов доступны отдельно. Пропуск данных
                  не считается отсутствием техники.
                </p>
              </InfoButton>
            </div>
          </div>
          <div
            className="overview-attention-tabs"
            role="tablist"
            aria-label="Проверка сигналов"
          >
            <button
              type="button"
              role="tab"
              aria-selected={attentionTab === "pending"}
              className={attentionTab === "pending" ? "active" : ""}
              onClick={() => setAttentionTab("pending")}
            >
              Требует внимания · {alerts.length}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={attentionTab === "reviewed"}
              className={attentionTab === "reviewed" ? "active" : ""}
              onClick={() => setAttentionTab("reviewed")}
            >
              Проверено · {reviewedAlerts.length}
            </button>
          </div>
          <div className="attention-content">
            {attentionTab === "reviewed" ? (
              reviewedAlerts.length ? (
                <div className="alert-list">
                  {reviewedAlerts.map((alert) => (
                    <button
                      key={alert.id}
                      onClick={() => setSelectedAlert(alert)}
                    >
                      <ShieldCheck size={17} />
                      <div>
                        <small className="alert-kind-label">
                          {alertKindName(alert.kind)}
                        </small>
                        <strong>{alert.details.title}</strong>
                        <p>
                          {displayFindingMessage(
                            alert.details.message,
                            alert.kind,
                            alert.details.classes,
                          )}
                        </p>
                        <small>
                          {alert.details.review?.decision === "dismissed"
                            ? "Ложный сигнал"
                            : "Подтверждён оператором"}{" "}
                          · {date(alert.last_seen)}
                        </small>
                      </div>
                      <ArrowUpRight size={16} />
                    </button>
                  ))}
                </div>
              ) : (
                <p className="overview-alert-empty">
                  Проверенных сигналов пока нет.
                </p>
              )
            ) : !alerts.length ? (
              <Empty
                icon={ShieldCheck}
                title="Открытых сигналов нет"
                text={
                  cards.some((c) => c.observation)
                    ? "В доступных оценках нет открытых отклонений."
                    : "Для проверки нужны наблюдения и утверждённый план."
                }
              />
            ) : (
              <>
                {currentAlerts.length ? (
                  <div className="alert-list">
                    {currentAlerts.map((alert) => (
                      <button
                        key={alert.id}
                        onClick={() => setSelectedAlert(alert)}
                      >
                        <div className={"alert-dot " + alert.severity} />
                        <div>
                          <small className="alert-kind-label">
                            {alertKindName(alert.kind)}
                          </small>
                          <strong>{alert.details.title}</strong>
                          <p>
                            {displayFindingMessage(
                              alert.details.message,
                              alert.kind,
                              alert.details.classes,
                            )}
                          </p>
                          <AlertBreakdown
                            alert={alert}
                            catalog={catalog}
                            compact
                          />
                          <small>{date(alert.last_seen)}</small>
                        </div>
                        <ArrowUpRight size={16} />
                      </button>
                    ))}
                  </div>
                ) : (
                  <p className="overview-alert-empty">
                    По незавершённым этапам открытых сигналов нет.
                  </p>
                )}
                {completedAlerts.length > 0 && (
                  <details className="overview-archive">
                    <summary>
                      Сигналы завершённых этапов ({completedAlerts.length})
                    </summary>
                    <div className="alert-list">
                      {completedAlerts.map((alert) => (
                        <button
                          key={alert.id}
                          onClick={() => setSelectedAlert(alert)}
                        >
                          <div className={"alert-dot " + alert.severity} />
                          <div>
                            <small className="alert-kind-label">
                              {alertKindName(alert.kind)}
                            </small>
                            <strong>{alert.details.title}</strong>
                            <p>
                              {displayFindingMessage(
                                alert.details.message,
                                alert.kind,
                                alert.details.classes,
                              )}
                            </p>
                            <AlertBreakdown
                              alert={alert}
                              catalog={catalog}
                              compact
                            />
                            <small>{date(alert.last_seen)}</small>
                          </div>
                          <ArrowUpRight size={16} />
                        </button>
                      ))}
                    </div>
                  </details>
                )}
              </>
            )}
          </div>
        </section>
        <div id="overview-composition" />
      </div>
      <PresenceCharts
        project={project}
        catalog={catalog}
        cards={cards}
        plan={monitor?.plan || null}
        workStates={monitor?.work_states || {}}
        asOf={now}
        preferredSourceId={statisticsSourceId}
        preferredWorkId={statisticsWorkId}
      />
      {selectedAlert && (
        <AlertReviewModal
          alert={selectedAlert}
          catalog={catalog}
          onClose={() => setSelectedAlert(null)}
          run={run}
          quiet={quiet}
        />
      )}
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Этапы строительства</h2>
            <span>Ресурсное соответствие и фактический статус разделены</span>
          </div>
          <div className="panel-heading-actions">
            <button className="text-button" onClick={() => navigate("plan")}>
              Перейти к плану
              <ArrowRight size={16} />
            </button>
            <InfoButton title="Этапы строительства">
              <p>
                Потребность в технике берётся из плана. Фактическое завершение
                этапа оператор отмечает вручную; наличие машины само по себе не
                подтверждает выполненный объём.
              </p>
            </InfoButton>
          </div>
        </div>
        {works.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Код / этап</th>
                  <th>Период, МСК</th>
                  <th>Требуется машин</th>
                  <th>Ручной статус</th>
                </tr>
              </thead>
              <tbody>
                {works.slice(0, 8).map((w) => (
                  <tr key={w.id}>
                    <td>
                      <code>{w.code}</code>
                      <strong>{w.title}</strong>
                    </td>
                    <td>
                      {date(w.starts_at)} - {date(w.ends_at)}
                    </td>
                    <td>
                      {Object.values(w.resources).reduce((a, b) => a + b, 0)}
                    </td>
                    <td>
                      <Badge
                        tone={
                          monitor?.work_states[w.id] === "completed"
                            ? "green"
                            : "neutral"
                        }
                      >
                        {stateName[monitor?.work_states[w.id] || "planned"]}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            icon={CalendarDays}
            title="План станет основой мониторинга"
            text="Импортируйте CSV или создайте этапы вручную из справочника работ."
            action={
              <button className="text-button" onClick={() => navigate("plan")}>
                Настроить план
                <ArrowRight size={17} />
              </button>
            }
          />
        )}
      </section>
    </>
  );
}
function Boxes({
  detections,
  catalog,
}: {
  detections: NonNullable<
    Monitor["cards"][number]["observation"]
  >["detections"];
  catalog: Catalog;
}) {
  return (
    <div className="boxes">
      <FrameDetections
        detections={detections}
        showFrames={true}
        showLabels={true}
        nameForClass={(id) =>
          catalog.classes.find((item) => String(item.id) === id)?.name ||
          "Класс " + id
        }
        colorForClass={equipmentColor}
      />
    </div>
  );
}
function PlanPage({
  project,
  catalog,
  monitor,
  revision,
  run,
  quiet,
  onOpenOverview,
  onOpenSources,
}: {
  project: Project;
  catalog: Catalog;
  monitor: Monitor | null;
  revision: number;
  onOpenOverview: () => void;
  onOpenSources: () => void;
  run: Run;
  quiet: Quiet;
}) {
  const { data, error, setData } = useData<{
    plans: Plan[];
    current_plan_id: string | null;
    work_states: Record<string, string>;
  }>(`/projects/${project.id}/plans`, revision);
  const [editing, setEditing] = useState(false),
    [editedWorks, setEditedWorks] = useState<Work[]>([]),
    [collapsedEditorWorkIds, setCollapsedEditorWorkIds] = useState<Set<string>>(
      new Set(),
    ),
    [shift, setShift] = useState<Work | null>(null),
    [shiftEnd, setShiftEnd] = useState(""),
    [cascade, setCascade] = useState(true),
    [preview, setPreview] = useState<{
      changes: {
        code: string;
        title: string;
        before: string[];
        after: string[];
      }[];
    } | null>(null),
    [localError, setLocalError] = useState("");
  const plan = data?.plans.find((p) => p.id === data.current_plan_id);
  const [clockNow, setClockNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setClockNow(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);
  const todayMoscow = new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Europe/Moscow",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
  const startDay = (work: Work) => inputDate(work.starts_at).slice(0, 10);
  const planningClassIds = [
    ...new Set([...project.class_ids, ...catalog.planning_class_ids]),
  ].sort((a, b) => a - b);
  const emptyResources = () =>
    Object.fromEntries(planningClassIds.map((c) => [String(c), 0]));
  const [pendingStatuses, setPendingStatuses] = useState<Set<string>>(
    new Set(),
  );
  const statusLocks = useRef(new Set<string>());
  const stageDrag = useRef<{ x: number; y: number; active: boolean } | null>(
    null,
  );
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const moveWork = (id: string, position: number) =>
    setEditedWorks((works) => {
      const from = works.findIndex((w) => w.id === id);
      if (
        from < 0 ||
        position < 0 ||
        position >= works.length ||
        from === position
      )
        return works;
      const moved = [...works];
      moved.splice(position, 0, moved.splice(from, 1)[0]);
      return moved;
    });
  const updateStatus = async (work: Work, status: string, note = "") => {
    if (statusLocks.current.has(work.id)) return;
    statusLocks.current.add(work.id);
    setPendingStatuses(new Set(statusLocks.current));
    try {
      await run(
        async () => {
          const result = await post<{ status: string; warnings: string[] }>(
            `/projects/${project.id}/works/${work.id}/status`,
            { status, note },
          );
          setData((previous) =>
            previous
              ? {
                  ...previous,
                  work_states: {
                    ...previous.work_states,
                    [work.id]: result.status,
                  },
                }
              : previous,
          );
          setLocalError(result.warnings.join("; "));
        },
        status === "in_progress"
          ? "Этап начат досрочно, дата плана обновлена"
          : status === "completed"
            ? "Завершение сохранено в журнале"
            : "Завершение отменено",
      );
    } finally {
      statusLocks.current.delete(work.id);
      setPendingStatuses(new Set(statusLocks.current));
    }
  };
  const hours =
    shift && shiftEnd
      ? (Date.parse(shiftEnd + ":00+03:00") - Date.parse(shift.ends_at)) /
        3600000
      : 0;
  const validShift =
    !!shift &&
    !!shiftEnd &&
    Number.isFinite(hours) &&
    hours !== 0 &&
    Date.parse(shiftEnd + ":00+03:00") > Date.parse(shift.starts_at);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const openEditor = (works: Work[]) => {
    const prepared = structuredClone(works).map((w) => ({
      ...w,
      resources: { ...emptyResources(), ...w.resources },
    }));
    setEditedWorks(prepared);
    setCollapsedEditorWorkIds(
      new Set(prepared.length >= 10 ? prepared.map((w) => w.id) : []),
    );
    setEditing(true);
  };
  const addWork = () => {
    const start = new Date();
    start.setMinutes(0, 0, 0);
    const end = new Date(start.getTime() + 86400e3);
    const id = crypto.randomUUID();
    setEditedWorks((v) => [
      ...v,
      {
        id,
        code: "",
        title: "",
        starts_at: start.toISOString(),
        ends_at: end.toISOString(),
        resources: emptyResources(),
      },
    ]);
    setCollapsedEditorWorkIds((ids) => {
      const next = new Set(ids);
      next.delete(id);
      return next;
    });
    requestAnimationFrame(() =>
      document
        .querySelector(`[data-work-id="${id}"]`)
        ?.scrollIntoView({ behavior: "smooth", block: "start" }),
    );
  };
  const edit = () => openEditor(plan?.works || []);
  const change = (id: string, patch: Partial<Work>) =>
    setEditedWorks((v) => v.map((w) => (w.id === id ? { ...w, ...patch } : w)));
  const [confirmWork, setConfirmWork] = useState<Work | null>(null);
  const [bindingWorkIds, setBindingWorkIds] = useState<string[]>([]);
  const [bindingSourceId, setBindingSourceId] = useState("");
  const [bindingOpen, setBindingOpen] = useState(false);
  const [bindingBusy, setBindingBusy] = useState(false);
  const [bindingError, setBindingError] = useState("");
  const [sourceCreateOpen, setSourceCreateOpen] = useState(false);
  const [createdSource, setCreatedSource] = useState<Source | null>(null);
  const [zoneSource, setZoneSource] = useState<Source | null>(null);
  const sourceCards: Monitor["cards"] = [...(monitor?.cards || [])];
  if (
    createdSource &&
    !sourceCards.some((card) => card.source.id === createdSource.id)
  ) {
    sourceCards.push({
      source: createdSource,
      binding: null,
      observation: null,
      assessment: null,
      stale_plan: false,
      stale_context: false,
    });
  }
  const openBinding = (workId: string) => {
    setBindingWorkIds([workId]);
    setBindingSourceId(sourceCards[0]?.source.id || "");
    setBindingError("");
    setBindingOpen(true);
  };
  const saveBinding = async (all: boolean) => {
    if (!plan || !bindingSourceId || bindingBusy) return;
    const card = sourceCards.find((item) => item.source.id === bindingSourceId);
    if (!card) return;
    const selected = all ? plan.works.map((work) => work.id) : bindingWorkIds;
    if (!selected.length) return;
    setBindingBusy(true);
    setBindingError("");
    try {
      const latest = await api<Binding>(`/sources/${bindingSourceId}/bindings`);
      const planIds = new Set(plan.works.map((work) => work.id));
      const retained = latest.regions.flatMap((region) => {
        const work_ids = region.work_ids.filter((id) => planIds.has(id));
        return work_ids.length ? [{ ...region, work_ids }] : [];
      });
      const existing = new Set(retained.flatMap((region) => region.work_ids));
      const regions = [
        ...retained,
        ...selected
          .filter((id) => !existing.has(id))
          .map((id) => ({
            id: crypto.randomUUID(),
            name: `${plan.works.find((work) => work.id === id)?.title || "Этап"} · весь кадр`,
            work_ids: [id],
            polygon: fullFramePolygon,
            primary: true,
            visibility_confirmed: true,
            color: polygonColors[latest.regions.length % polygonColors.length],
            visible: true,
          })),
      ];
      await run(
        async () => {
          await post(`/sources/${bindingSourceId}/bindings`, {
            expected_revision: latest.revision,
            regions,
          });
          setBindingOpen(false);
        },
        `Источник привязан · ${selected
          .map((id) => plan.works.find((work) => work.id === id)?.title)
          .filter(Boolean)
          .join(", ")}. Анализ начнётся автоматически, когда источник готов.`,
      );
      if (card.source.preview_url) setZoneSource(card.source);
    } catch (error) {
      setBindingError((error as Error).message);
    } finally {
      setBindingBusy(false);
    }
  };
  const allEditorWorksCollapsed =
    editedWorks.length > 0 &&
    editedWorks.every((w) => collapsedEditorWorkIds.has(w.id));
  return (
    <>
      <PageHead
        eyebrow="ПЛАНИРОВАНИЕ"
        title="План работ"
        description="Этапы, сроки и требуемое количество техники."
      >
        <button className="button primary" onClick={onOpenOverview}>
          <Activity size={18} />
          Обзор объекта
        </button>
        <button
          className="button secondary plan-camera-list"
          onClick={onOpenSources}
        >
          <Camera size={20} /> Список камер
        </button>
        <a
          className="button secondary"
          href={`/api/v1/projects/${project.id}/plan-template`}
        >
          <ArrowDownToLine size={16} />
          Шаблон CSV
        </a>
        <button
          className="button secondary"
          onClick={() => fileRef.current?.click()}
        >
          <Upload size={16} />
          Импорт CSV
        </button>
        <button className="button primary" onClick={edit}>
          {plan ? <Pencil size={16} /> : <Plus size={16} />}
          {plan ? "Редактировать план" : "Создать план"}
        </button>
        {plan && (
          <a
            className="button secondary"
            href={`/api/v1/projects/${project.id}/plans/${plan.id}/export`}
          >
            <ArrowDownToLine size={16} /> Скачать план CSV
          </a>
        )}
        {plan && (
          <button
            className="button secondary destructive"
            onClick={() => setConfirmDelete(true)}
          >
            <Trash2 size={16} />
            Удалить план
          </button>
        )}
        <input
          style={{ display: "none" }}
          ref={fileRef}
          type="file"
          accept=".csv"
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) {
              const form = new FormData();
              form.append("file", f);
              quiet(() =>
                run(
                  () =>
                    api(`/projects/${project.id}/plan-imports`, {
                      method: "POST",
                      body: form,
                    }),
                  "План из CSV утверждён",
                ),
              );
              e.target.value = "";
            }
          }}
        />
      </PageHead>
      {(error || localError) && (
        <div className="banner danger">{error || localError}</div>
      )}
      <section className="panel">
        <div className={`panel-heading${plan ? " plan-panel-heading" : ""}`}>
          <div>
            <h2>
              {plan
                ? `Действующий план · версия ${plan.version}`
                : "Действующий план"}
            </h2>
            <span>Фактическое завершение этапов подтверждается вручную</span>
          </div>
          {plan && <span className="plan-sources-heading">Источники</span>}
          <div className="plan-heading-status">
            {plan && <Badge tone="green">Утверждён</Badge>}
            <InfoButton title="Действующий план">
              <p>
                План задаёт календарь и нужный состав техники. Источники
                привязываются к одному или нескольким этапам; зоны можно
                уточнить после привязки.
              </p>
              <p>Завершение этапа отмечается вручную.</p>
            </InfoButton>
          </div>
        </div>
        {plan ? (
          <div className="work-list">
            {plan.works.map((w) => (
              <div className="work-row" key={w.id}>
                <div className="work-code">{w.code}</div>
                <div className="work-main">
                  <h3>{w.title}</h3>
                  <p>
                    <Clock3 size={14} />
                    {date(w.starts_at)} - {date(w.ends_at)}
                  </p>
                  <div className="resource-tags">
                    {Object.entries(w.resources)
                      .filter(([, n]) => n > 0)
                      .map(([c, n]) => (
                        <span key={c}>
                          {
                            catalog.classes.find((x) => String(x.id) === c)
                              ?.name
                          }{" "}
                          <b>{n}</b>
                        </span>
                      ))}
                  </div>
                </div>
                <div className="work-sources">
                  <div className="work-source-items">
                    {sourceCards
                      .filter((card) =>
                        card.binding?.regions.some((region) =>
                          region.work_ids.includes(w.id),
                        ),
                      )
                      .map((card) => (
                        <button
                          type="button"
                          className="work-source-tile"
                          aria-label={`${card.source.name}. Редактировать зоны`}
                          key={card.source.id}
                          title={`${card.source.name}. Редактировать зоны`}
                          onClick={() => setZoneSource(card.source)}
                        >
                          {card.source.preview_url ? (
                            <img src={card.source.preview_url} alt="" />
                          ) : (
                            <Camera size={17} />
                          )}
                          <span>{card.source.name}</span>
                        </button>
                      ))}
                  </div>
                  <button
                    type="button"
                    className={`work-source-add${sourceCards.some((card) => card.binding?.regions.some((region) => region.work_ids.includes(w.id))) ? "" : " work-source-add-empty"}`}
                    aria-label={`Добавить камеру или видео к этапу ${w.title}`}
                    onClick={() => openBinding(w.id)}
                  >
                    <span className="work-source-add-icon">
                      <Plus size={18} />
                    </span>
                    <span>Добавить камеру</span>
                  </button>
                </div>
                <div className="work-actions">
                  <Badge
                    tone={
                      data?.work_states[w.id] === "completed"
                        ? "green"
                        : "neutral"
                    }
                  >
                    {data?.work_states[w.id] === "completed"
                      ? stateName.completed
                      : data?.work_states[w.id] === "in_progress" ||
                          (Date.parse(w.starts_at) <= clockNow &&
                            Date.parse(w.ends_at) > clockNow)
                        ? "Действует"
                        : stateName.planned}
                  </Badge>
                  <div>
                    {data?.work_states[w.id] !== "completed" ? (
                      <>
                        {startDay(w) > todayMoscow &&
                          data?.work_states[w.id] !== "in_progress" && (
                            <button
                              className="text-button"
                              disabled={pendingStatuses.has(w.id)}
                              onClick={() =>
                                quiet(() => updateStatus(w, "in_progress"))
                              }
                            >
                              Начать досрочно
                            </button>
                          )}
                        <button
                          className="text-button"
                          disabled={pendingStatuses.has(w.id)}
                          onClick={() => setConfirmWork(w)}
                        >
                          Завершить
                        </button>
                      </>
                    ) : (
                      <button
                        className="text-button"
                        disabled={pendingStatuses.has(w.id)}
                        onClick={() =>
                          quiet(() =>
                            updateStatus(
                              w,
                              "planned",
                              "Отмена завершения оператором",
                            ),
                          )
                        }
                      >
                        Отменить завершение
                      </button>
                    )}
                    <button
                      className="text-button"
                      onClick={() => {
                        setShift(w);
                        setShiftEnd(
                          inputDate(
                            new Date(
                              Date.parse(w.ends_at) + 86400000,
                            ).toISOString(),
                          ),
                        );
                        setPreview(null);
                      }}
                    >
                      Сдвинуть
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <Empty
            icon={CalendarDays}
            title="Утвердите первый план"
            text="Создайте этапы вручную или загрузите CSV. Все классы должны иметь количество; для ненужной техники укажите 0."
            action={
              <button className="button primary" onClick={edit}>
                <Plus size={16} />
                Создать план
              </button>
            }
          />
        )}
      </section>
      {bindingOpen && plan && (
        <Modal
          title="Источники этапов"
          onClose={() => {
            if (!bindingBusy) setBindingOpen(false);
          }}
        >
          <p className="hint">
            Выберите камеру или видео и этапы. После добавления можно уточнить
            область кадра полигонами.
          </p>
          {bindingError && (
            <div className="banner danger" role="alert">
              {bindingError}
            </div>
          )}
          <div className="binding-source-control">
            <label>
              Источник
              <select
                value={bindingSourceId}
                disabled={bindingBusy}
                onChange={(event) => setBindingSourceId(event.target.value)}
              >
                {!sourceCards.length && (
                  <option value="">Источников пока нет</option>
                )}
                {sourceCards.map((card) => (
                  <option key={card.source.id} value={card.source.id}>
                    {card.source.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="button secondary"
              type="button"
              disabled={bindingBusy}
              onClick={() => {
                setBindingOpen(false);
                setSourceCreateOpen(true);
              }}
            >
              <Plus size={16} /> Новый источник
            </button>
          </div>
          <div className="binding-stage-list">
            {plan.works.map((work) => (
              <label key={work.id} className="checkbox">
                <input
                  type="checkbox"
                  checked={bindingWorkIds.includes(work.id)}
                  disabled={bindingBusy}
                  onChange={(event) =>
                    setBindingWorkIds((ids) =>
                      event.target.checked
                        ? [...ids, work.id]
                        : ids.filter((id) => id !== work.id),
                    )
                  }
                />
                {work.code} · {work.title}
              </label>
            ))}
          </div>
          <div className="form-actions">
            <button
              className="button secondary"
              type="button"
              disabled={bindingBusy}
              onClick={() => setBindingOpen(false)}
            >
              Отмена
            </button>
            <button
              className="button secondary"
              type="button"
              disabled={bindingBusy || !bindingSourceId}
              onClick={() => saveBinding(true)}
            >
              Добавить на все
            </button>
            <button
              className="button primary"
              type="button"
              disabled={
                bindingBusy || !bindingSourceId || !bindingWorkIds.length
              }
              onClick={() => saveBinding(false)}
            >
              Добавить
            </button>
          </div>
        </Modal>
      )}
      {sourceCreateOpen && (
        <SourceCreateModal
          project={project}
          run={run}
          onClose={() => {
            setSourceCreateOpen(false);
            setBindingOpen(true);
          }}
          onCreated={(source) => {
            setCreatedSource(source);
            setBindingSourceId(source.id);
            setSourceCreateOpen(false);
            setBindingOpen(true);
          }}
        />
      )}
      {zoneSource && plan && (
        <ZoneEditor
          source={zoneSource}
          works={plan.works}
          workStates={data?.work_states || {}}
          onClose={() => setZoneSource(null)}
          onSave={(binding) =>
            run(
              () => post(`/sources/${zoneSource.id}/bindings`, binding),
              "Зоны сохранены",
            )
          }
        />
      )}
      {data && data.plans.length > 0 && (
        <section className="panel">
          <div className="panel-heading">
            <h2>История версий</h2>
            <InfoButton title="История версий">
              <p>
                Каждое изменение плана сохраняет отдельную версию. Анализ и
                сохранённые свидетельства продолжают ссылаться на версию, с
                которой были получены.
              </p>
            </InfoButton>
          </div>
          <div className="version-list">
            {data.plans.map((p) => (
              <div key={p.id}>
                <span>Версия {p.version}</span>
                <span>{p.works.length} этапов</span>
                <Badge
                  tone={p.id === project.current_plan_id ? "green" : "neutral"}
                >
                  {p.id === project.current_plan_id
                    ? "Действует"
                    : p.status === "deleted"
                      ? "Удалён"
                      : "Архив"}
                </Badge>
                <button
                  className="text-button"
                  onClick={() => openEditor(p.works)}
                >
                  Открыть
                </button>
                <a
                  className="text-button"
                  href={`/api/v1/projects/${project.id}/plans/${p.id}/export`}
                >
                  Скачать CSV
                </a>
              </div>
            ))}
          </div>
        </section>
      )}
      {editing && (
        <Modal title="Редактор плана" wide onClose={() => setEditing(false)}>
          <p className="hint">
            Время - Москва (UTC+3). Количество должно соответствовать плану:
            недобор и избыток считаются отклонениями.
          </p>
          <p className="hint">
            Перетащите карточку за ручку или измените номер этапа. Порядок
            сохраняется в плане; код работы и её статус не меняются.
          </p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              quiet(() =>
                run(async () => {
                  const response = await post<Plan>(
                    `/projects/${project.id}/plans`,
                    {
                      works: editedWorks,
                      base_plan_id: project.current_plan_id,
                      additional_class_ids: planningClassIds.filter(
                        (c) => !project.class_ids.includes(c),
                      ),
                    },
                  );
                  setEditing(false);
                  if (response.validation?.warnings.length)
                    setLocalError(
                      response.validation.warnings
                        .map((w) => w.message)
                        .join("; "),
                    );
                }, "План утверждён"),
              );
            }}
          >
            <div className="plan-editor-toolbar">
              <span>
                Этапов: <b>{editedWorks.length}</b>
              </span>
              <button
                type="button"
                className="button primary"
                onClick={addWork}
              >
                <Plus size={16} />
                Добавить этап
              </button>
              <button
                type="button"
                className="button secondary"
                disabled={!editedWorks.length}
                onClick={() =>
                  setCollapsedEditorWorkIds(
                    allEditorWorksCollapsed
                      ? new Set()
                      : new Set(editedWorks.map((w) => w.id)),
                  )
                }
              >
                {allEditorWorksCollapsed ? "Развернуть все" : "Свернуть все"}
              </button>
            </div>
            <div className="plan-editor-list">
              {editedWorks.map((w, i) => {
                const collapsed = collapsedEditorWorkIds.has(w.id);
                return (
                  <div
                    className={`plan-editor-work${collapsed ? " collapsed" : ""}${dropTarget === w.id ? " drop-target" : ""}`}
                    key={w.id}
                    data-work-id={w.id}
                  >
                    <div className="plan-editor-heading">
                      <button
                        type="button"
                        className="icon-button stage-drag-handle"
                        aria-label={`Перетащить этап ${i + 1}`}
                        onPointerDown={(event) => {
                          if (event.button !== 0) return;
                          event.currentTarget.setPointerCapture(
                            event.pointerId,
                          );
                          stageDrag.current = {
                            x: event.clientX,
                            y: event.clientY,
                            active: false,
                          };
                        }}
                        onPointerMove={(event) => {
                          const drag = stageDrag.current;
                          if (!drag) return;
                          if (
                            Math.hypot(
                              event.clientX - drag.x,
                              event.clientY - drag.y,
                            ) < 5 &&
                            !drag.active
                          )
                            return;
                          drag.active = true;
                          const target = document
                            .elementFromPoint(event.clientX, event.clientY)
                            ?.closest<HTMLElement>("[data-work-id]");
                          setDropTarget(target?.dataset.workId || null);
                          const modal = event.currentTarget.closest(".modal");
                          if (modal) {
                            const bounds = modal.getBoundingClientRect();
                            if (event.clientY < bounds.top + 60)
                              modal.scrollTop -= 24;
                            if (event.clientY > bounds.bottom - 60)
                              modal.scrollTop += 24;
                          }
                        }}
                        onPointerUp={(event) => {
                          if (stageDrag.current?.active) {
                            const target = document
                              .elementFromPoint(event.clientX, event.clientY)
                              ?.closest<HTMLElement>("[data-work-id]");
                            moveWork(
                              w.id,
                              editedWorks.findIndex(
                                (item) => item.id === target?.dataset.workId,
                              ),
                            );
                          }
                          stageDrag.current = null;
                          setDropTarget(null);
                        }}
                        onLostPointerCapture={() => {
                          stageDrag.current = null;
                          setDropTarget(null);
                        }}
                        onPointerCancel={() => {
                          stageDrag.current = null;
                          setDropTarget(null);
                        }}
                      >
                        <GripVertical size={20} />
                      </button>
                      <label className="stage-order">
                        Этап
                        <select
                          aria-label={`Номер этапа ${w.code || i + 1}`}
                          value={i}
                          onChange={(e) =>
                            moveWork(w.id, Number(e.target.value))
                          }
                        >
                          {editedWorks.map((_, position) => (
                            <option key={position} value={position}>
                              {position + 1}
                            </option>
                          ))}
                        </select>
                      </label>
                      <button
                        type="button"
                        className="plan-stage-summary"
                        aria-expanded={!collapsed}
                        aria-label={`${collapsed ? "Развернуть" : "Свернуть"} этап ${i + 1}`}
                        onClick={() =>
                          setCollapsedEditorWorkIds((ids) => {
                            const next = new Set(ids);
                            if (collapsed) next.delete(w.id);
                            else next.add(w.id);
                            return next;
                          })
                        }
                      >
                        {collapsed ? (
                          <ChevronRight size={18} />
                        ) : (
                          <ChevronDown size={18} />
                        )}
                        <span>
                          {w.code && w.title
                            ? `${w.code} · ${w.title}`
                            : "Новый этап без выбранной работы"}
                        </span>
                      </button>
                      <button
                        type="button"
                        className="icon-button"
                        aria-label={`Поднять этап ${i + 1}`}
                        disabled={i === 0}
                        onClick={() => moveWork(w.id, i - 1)}
                      >
                        <ArrowUp size={17} />
                      </button>
                      <button
                        type="button"
                        className="icon-button"
                        aria-label={`Опустить этап ${i + 1}`}
                        disabled={i === editedWorks.length - 1}
                        onClick={() => moveWork(w.id, i + 1)}
                      >
                        <ArrowDown size={17} />
                      </button>
                      <button
                        type="button"
                        className="icon-button"
                        aria-label="Удалить этап"
                        onClick={() => {
                          setEditedWorks((v) => v.filter((x) => x.id !== w.id));
                          setCollapsedEditorWorkIds((ids) => {
                            const next = new Set(ids);
                            next.delete(w.id);
                            return next;
                          });
                        }}
                      >
                        <Trash2 size={17} />
                      </button>
                    </div>
                    {!collapsed && (
                      <>
                        <CatalogSearch
                          works={catalog.works.filter(
                            (w) =>
                              !project.project_type_id ||
                              w.project_type_ids.includes(
                                project.project_type_id,
                              ),
                          )}
                          code={w.code}
                          onSelect={(row) =>
                            change(w.id, {
                              code: row.code,
                              title: row.title,
                              ...(row.code !== w.code
                                ? {
                                    resources: {
                                      ...emptyResources(),
                                      ...row.resource_preset.counts,
                                    },
                                  }
                                : {}),
                            })
                          }
                        />
                        <div className="form-grid">
                          <label>
                            Код
                            <input
                              required
                              pattern="[0-9]+(\.[0-9]+)*"
                              value={w.code}
                              onChange={(e) =>
                                change(w.id, { code: e.target.value })
                              }
                            />
                          </label>
                          <label>
                            Название
                            <input
                              required
                              value={w.title}
                              onChange={(e) =>
                                change(w.id, { title: e.target.value })
                              }
                            />
                          </label>
                          <StageDatePicker
                            label="Начало этапа, МСК"
                            part="начала"
                            value={inputDate(w.starts_at)}
                            onChange={(value) =>
                              change(w.id, { starts_at: isoDate(value) })
                            }
                          />
                          <StageDatePicker
                            label="Конец этапа, МСК"
                            value={inputDate(w.ends_at)}
                            onChange={(value) =>
                              change(w.id, { ends_at: isoDate(value) })
                            }
                          />
                        </div>
                        <details className="details" open>
                          <summary>
                            Требуемое количество техники ·{" "}
                            {planningClassIds.length} классов
                          </summary>
                          <WorkResourcePreset
                            work={catalog.works.find(
                              (row) => row.code === w.code,
                            )}
                            catalog={catalog}
                            onApply={(counts) =>
                              change(w.id, {
                                resources: { ...emptyResources(), ...counts },
                              })
                            }
                          />
                          <div className="resource-inputs">
                            {catalog.classes
                              .filter((c) => planningClassIds.includes(c.id))
                              .sort((a, b) =>
                                a.name.localeCompare(b.name, "ru"),
                              )
                              .map((c) => (
                                <label key={c.id}>
                                  <span className="resource-input-name">
                                    {equipmentIconUrl(c.id) ? (
                                      <img
                                        src={equipmentIconUrl(c.id)!}
                                        alt=""
                                        aria-hidden="true"
                                      />
                                    ) : (
                                      <span
                                        className="resource-input-icon-fallback"
                                        aria-hidden="true"
                                      >
                                        ·
                                      </span>
                                    )}
                                    {c.name}
                                  </span>
                                  <input
                                    type="number"
                                    min="0"
                                    max="10000"
                                    required
                                    value={w.resources[String(c.id)] ?? 0}
                                    onChange={(e) =>
                                      change(w.id, {
                                        resources: {
                                          ...w.resources,
                                          [String(c.id)]: Number(
                                            e.target.value,
                                          ),
                                        },
                                      })
                                    }
                                  />
                                </label>
                              ))}
                          </div>
                        </details>
                      </>
                    )}
                  </div>
                );
              })}
            </div>
            <button
              type="button"
              className="button secondary"
              onClick={addWork}
            >
              <Plus size={16} />
              Добавить этап
            </button>
            <div className="form-actions">
              <button
                type="button"
                className="button secondary"
                onClick={() => setEditing(false)}
              >
                Отмена
              </button>
              <button className="button primary" disabled={!editedWorks.length}>
                Утвердить план
                <Check size={17} />
              </button>
            </div>
          </form>
        </Modal>
      )}
      {shift && plan && (
        <Modal
          title={`Сдвиг этапа ${shift.code}`}
          onClose={() => setShift(null)}
        >
          <p>{shift.title}</p>
          <StageDatePicker
            value={shiftEnd}
            onChange={(value) => {
              setShiftEnd(value);
              setPreview(null);
            }}
          />
          <label className="checkbox">
            <input
              type="checkbox"
              checked={cascade}
              onChange={(e) => {
                setCascade(e.target.checked);
                setPreview(null);
              }}
            />
            Сдвинуть следующие этапы, если нарушаются зависимости
          </label>
          <p className="hint">
            Текущее окончание: {date(shift.ends_at)}. Выберите новую дату в
            календаре. Следующие работы не сдвигаются раньше автоматически.
          </p>
          <button
            className="button secondary"
            disabled={!validShift}
            onClick={() =>
              quiet(() =>
                run(
                  async () =>
                    setPreview(
                      await post(`/plans/${plan.id}/shift-preview`, {
                        work_id: shift.id,
                        hours,
                        cascade,
                      }),
                    ),
                  "",
                ),
              )
            }
          >
            Предпросмотр
          </button>
          {preview && (
            <div className="shift-preview">
              {preview.changes.map((c) => (
                <div key={c.code}>
                  <b>
                    {c.code} {c.title}
                  </b>
                  <p>
                    {date(c.before[0])} - {date(c.before[1])}
                  </p>
                  <p className="after">
                    <ArrowRight size={14} />
                    {date(c.after[0])} - {date(c.after[1])}
                  </p>
                </div>
              ))}
            </div>
          )}
          <div className="form-actions">
            <button
              className="button primary"
              disabled={!preview || !validShift}
              onClick={() =>
                quiet(() =>
                  run(async () => {
                    await post(`/projects/${project.id}/shift`, {
                      work_id: shift.id,
                      hours,
                      cascade,
                      expected_plan_id: plan.id,
                    });
                    setShift(null);
                  }, "Создана новая версия плана"),
                )
              }
            >
              Применить новую версию
            </button>
          </div>
        </Modal>
      )}
      {confirmWork && (
        <Modal title="Завершить этап" onClose={() => setConfirmWork(null)}>
          <p>
            Вы подтверждаете завершение работы «{confirmWork.title}». Детекция
            техники не является основанием для автоматического завершения.
          </p>
          <div className="form-actions">
            <button
              className="button secondary"
              onClick={() => setConfirmWork(null)}
            >
              Отмена
            </button>
            <button
              className="button primary"
              disabled={pendingStatuses.has(confirmWork.id)}
              onClick={() =>
                quiet(() =>
                  (async () => {
                    await updateStatus(
                      confirmWork,
                      "completed",
                      "Подтверждено оператором в веб-интерфейсе",
                    );
                    setConfirmWork(null);
                  })(),
                )
              }
            >
              <Check size={17} />
              Подтвердить завершение
            </button>
          </div>
        </Modal>
      )}
      {confirmDelete && plan && (
        <Modal title="Удалить план" onClose={() => setConfirmDelete(false)}>
          <p>
            Действующий план будет снят с объекта. История версий и
            свидетельства сохранятся. Обработка по этому плану остановится.
          </p>
          <div className="form-actions">
            <button
              className="button secondary"
              onClick={() => setConfirmDelete(false)}
            >
              Отмена
            </button>
            <button
              className="button destructive"
              onClick={() =>
                quiet(() =>
                  run(async () => {
                    await api(`/projects/${project.id}/plan`, {
                      method: "DELETE",
                      body: JSON.stringify({ expected_plan_id: plan.id }),
                    });
                    setConfirmDelete(false);
                  }, "План удалён"),
                )
              }
            >
              <Trash2 size={16} />
              Удалить план
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}

function SourceCreateModal({
  project,
  run,
  onClose,
  onCreated,
}: {
  project: Project;
  run: Run;
  onClose: () => void;
  onCreated: (source: Source) => void;
}) {
  const [mode, setMode] = useState("file"),
    [name, setName] = useState(""),
    [uri, setUri] = useState(""),
    [start, setStart] = useState(""),
    [interval, setIntervalValue] = useState(1),
    [file, setFile] = useState<File | null>(null),
    [demoLoop, setDemoLoop] = useState(false),
    [demoLoopHours, setDemoLoopHours] = useState(8),
    [demoLoopMinutes, setDemoLoopMinutes] = useState(0),
    [submitting, setSubmitting] = useState(false),
    [error, setError] = useState("");
  const selectedFileIsVideo = !!file?.name.match(/\.(mp4|mkv|avi|mov)$/i);
  const submit = async () => {
    if (submitting) return;
    setSubmitting(true);
    setError("");
    try {
      let created!: { source: Source };
      await run(async () => {
        if (mode === "rtsp") {
          created = await post<{ source: Source }>(
            `/projects/${project.id}/sources`,
            {
              name,
              uri,
              sample_seconds: interval,
            },
          );
        } else {
          if (!file) throw new Error("Выберите файл");
          if (demoLoop && !start)
            throw new Error(
              "Для тестового зацикливания укажите начало демонстрации",
            );
          const demoDuration = demoDurationSeconds(
            demoLoopHours,
            demoLoopMinutes,
          );
          if (demoLoop && demoDuration <= 0)
            throw new Error(
              "Длительность демонстрации должна быть не меньше одной минуты",
            );
          const form = new FormData();
          form.append("file", file);
          form.append("name", name || file.name);
          form.append("sample_seconds", String(interval));
          if (start) form.append("capture_start", isoDate(start));
          if (demoLoop) {
            form.append("demo_loop_enabled", "true");
            form.append("demo_loop_duration_seconds", String(demoDuration));
          }
          created = await api<{ source: Source }>(
            `/projects/${project.id}/media`,
            {
              method: "POST",
              body: form,
            },
          );
        }
        onCreated(created.source);
      }, "Источник добавлен. Привяжите его к этапу для анализа.");
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setSubmitting(false);
    }
  };
  return (
    <Modal title="Новый источник" onClose={onClose}>
      {error && (
        <div className="banner danger" role="alert">
          {error}
        </div>
      )}
      <div className="segmented">
        <button
          className={mode === "file" ? "active" : ""}
          onClick={() => setMode("file")}
        >
          <Upload size={16} />
          Файл
        </button>
        <button
          className={mode === "rtsp" ? "active" : ""}
          onClick={() => setMode("rtsp")}
        >
          <Camera size={16} />
          RTSP / HTTPS
        </button>
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <label>
          Название
          <input
            required={mode === "rtsp"}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Котлован · камера 01"
          />
        </label>
        {mode === "file" ? (
          <>
            <label className="file-drop">
              <Upload size={29} />
              <strong>{file?.name || "Выберите видео или изображение"}</strong>
              <small>MP4, MKV, AVI, MOV, PNG, JPG, WebP · до 2 GiB</small>
              <input
                type="file"
                required
                accept=".mp4,.mkv,.avi,.mov,.png,.jpg,.jpeg,.webp"
                onChange={(e) => {
                  const next = e.target.files?.[0] || null;
                  setFile(next);
                  if (!next?.name.match(/\.(mp4|mkv|avi|mov)$/i))
                    setDemoLoop(false);
                }}
              />
            </label>
            <div className="stable-datetime-field">
              <span>Дата и время начала записи, МСК</span>
              <StableDateTimeInput
                required={demoLoop}
                value={start}
                onChange={setStart}
              />
            </div>
            <p className="hint">
              Если время неизвестно, оставьте пустым. Просмотр будет доступен,
              сопоставление с календарём - нет.
            </p>
            {selectedFileIsVideo && (
              <div className="demo-loop-box">
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={demoLoop}
                    onChange={(event) => setDemoLoop(event.target.checked)}
                  />
                  <strong>Циклически повторять видео</strong>
                </label>
                {demoLoop && (
                  <>
                    <p className="demo-loop-warning">
                      Тестовый демо-режим: видео будет циклически повторяться.
                      Результаты не являются непрерывной реальной съёмкой.
                    </p>
                    <div className="demo-duration-fields">
                      <label>
                        Часы
                        <input
                          type="number"
                          min="0"
                          step="1"
                          required
                          value={demoLoopHours}
                          onChange={(event) =>
                            setDemoLoopHours(Number(event.target.value))
                          }
                        />
                      </label>
                      <label>
                        Минуты
                        <input
                          type="number"
                          min={demoLoopHours === 0 ? "1" : "0"}
                          max="59"
                          step="1"
                          required
                          value={demoLoopMinutes}
                          onChange={(event) =>
                            setDemoLoopMinutes(Number(event.target.value))
                          }
                        />
                      </label>
                    </div>
                  </>
                )}
                <p className="hint">
                  Видео будет повторяться, создавая результаты на виртуальной
                  временной шкале. Если заданная длительность выходит за границу
                  привязанного этапа, анализ автоматически остановится в момент
                  его окончания.
                </p>
              </div>
            )}
          </>
        ) : (
          <>
            <label>
              Адрес RTSP, RTSPS или HTTPS
              <input
                required
                type="text"
                value={uri}
                onChange={(e) => setUri(e.target.value)}
                placeholder="rtsp://camera/stream или https://camera/live.m3u8"
                autoComplete="off"
              />
            </label>
            <p className="hint">
              Укажите прямую ссылку на видеопоток. Данные подключения хранятся в
              зашифрованном виде.
            </p>
          </>
        )}
        <label>
          Обрабатывать один кадр каждые, секунд
          <input
            type="number"
            min="1"
            max="3600"
            step="1"
            required
            value={interval}
            onChange={(e) => setIntervalValue(Number(e.target.value))}
          />
        </label>
        <div className="form-actions">
          <button
            className="button secondary"
            type="button"
            onClick={() => onClose()}
          >
            Отмена
          </button>
          <button className="button primary" disabled={submitting}>
            <Plus size={17} />
            Добавить источник
          </button>
        </div>
      </form>
    </Modal>
  );
}

function SourcesPage({
  project,
  catalog,
  monitor,
  revision,
  onOpenStatistics,
  run,
  quiet,
}: {
  project: Project;
  catalog: Catalog;
  monitor: Monitor | null;
  revision: number;
  onOpenStatistics: (sourceId: string) => void;
  run: Run;
  quiet: Quiet;
}) {
  const { data: sources, error } = useData<Source[]>(
      `/projects/${project.id}/sources`,
      revision,
      4000,
    ),
    { data: jobs } = useData<Job[]>(
      `/projects/${project.id}/jobs`,
      revision,
      4000,
    );
  const [adding, setAdding] = useState(false),
    [zoneSource, setZoneSource] = useState<Source | null>(null),
    [viewSource, setViewSource] = useState<Source | null>(null),
    [editSource, setEditSource] = useState<Source | null>(null),
    [deleteSource, setDeleteSource] = useState<Source | null>(null);
  return (
    <>
      <PageHead
        eyebrow="ИСТОЧНИКИ НАБЛЮДЕНИЙ"
        title="Камеры и видео"
        description="Подключение, зоны контроля и обработка в фоновом режиме."
      >
        <Badge>{sources?.length || 0} / 20 источников</Badge>
        <button className="button primary" onClick={() => setAdding(true)}>
          <Plus size={17} />
          Добавить источник
        </button>
      </PageHead>
      {error && <div className="banner danger">{error}</div>}
      {sources?.length ? (
        <div className="source-grid">
          {sources.map((source) => {
            const job = jobs?.find(
              (j) =>
                j.source_id === source.id &&
                ["queued", "running"].includes(j.status),
            );
            const latestObservation = monitor?.cards.find(
              (card) => card.source.id === source.id,
            )?.observation;
            return (
              <section className="panel source-card" key={source.id}>
                <div className="source-image">
                  {source.preview_url ? (
                    <img src={source.preview_url} alt={`Кадр ${source.name}`} />
                  ) : (
                    <div className="source-placeholder">
                      <Camera size={40} />
                      <span>
                        {job ? "Получаем первый кадр…" : "Кадр недоступен"}
                      </span>
                    </div>
                  )}
                  <span className="camera-kind">
                    {source.kind === "rtsp"
                      ? "СЕТЕВОЙ ПОТОК"
                      : source.kind === "video"
                        ? "ВИДЕОФАЙЛ"
                        : "ИЗОБРАЖЕНИЕ"}
                  </span>
                  {source.preview_url && (
                    <button
                      className="preview-play"
                      aria-label="Открыть источник"
                      onClick={() => setViewSource(source)}
                    >
                      <Play size={23} />
                    </button>
                  )}
                </div>
                <div className="source-body">
                  <div className="source-title">
                    <h2>{source.name}</h2>
                    <div className="source-title-actions">
                      <Badge
                        tone={
                          source.status === "failed"
                            ? "red"
                            : source.status === "running"
                              ? "green"
                              : "neutral"
                        }
                      >
                        {sourceStatusText(source)}
                      </Badge>
                      <button
                        className="icon-button"
                        aria-label={`Изменить источник ${source.name}`}
                        title="Изменить источник"
                        disabled={!!job && source.kind !== "rtsp"}
                        onClick={() => setEditSource(source)}
                      >
                        <Pencil size={16} />
                      </button>
                      <button
                        className="icon-button destructive"
                        aria-label={`Удалить источник ${source.name}`}
                        title="Удалить источник"
                        disabled={!!job}
                        onClick={() => setDeleteSource(source)}
                      >
                        <Trash2 size={16} />
                      </button>
                      <InfoButton title="Источник наблюдения">
                        <p>
                          Видео и камеры обрабатываются по кадрам с частотой не
                          выше 1 FPS. Для сравнения с планом задайте время
                          записи и привяжите этап.
                        </p>
                        <p>
                          Кнопка «Анализ» повторно запускает обработку
                          источника.
                        </p>
                      </InfoButton>
                    </div>
                  </div>
                  <div className="source-metadata">
                    <span>
                      {source.metadata_json.width
                        ? `${source.metadata_json.width} × ${source.metadata_json.height}`
                        : "Размер определяется"}
                    </span>
                    <span>
                      {source.metadata_json.duration_seconds
                        ? duration(source.metadata_json.duration_seconds)
                        : source.kind === "rtsp"
                          ? "Прямой источник"
                          : "-"}
                    </span>
                    <span>Кадр / {source.sample_seconds} с</span>
                  </div>
                  <p className="source-date">
                    <Clock3 size={14} />
                    {source.capture_start
                      ? date(source.capture_start)
                      : source.kind === "rtsp"
                        ? "Время по приёму сервером"
                        : "Время съёмки не задано"}
                  </p>
                  {source.error && (
                    <div className="inline-warning">{source.error}</div>
                  )}
                  {job && (
                    <div
                      className={
                        "progress" +
                        (source.kind === "rtsp" && job.kind === "analyze"
                          ? " indeterminate"
                          : "")
                      }
                    >
                      <div
                        style={{
                          width:
                            source.kind === "rtsp" && job.kind === "analyze"
                              ? "100%"
                              : Math.max(3, job.progress * 100) + "%",
                        }}
                      />
                      <span>
                        {source.kind === "rtsp" && job.kind === "probe"
                          ? sourceStatusText(source)
                          : source.kind === "rtsp" && job.kind === "analyze"
                            ? source.processing_state === "reconnecting"
                              ? "Восстанавливаем видеопоток"
                              : latestObservation
                                ? `Обработка потока · последний кадр ${date(latestObservation.captured_at)}`
                                : "Ожидаем первый кадр потока"
                            : `${job.kind === "probe" ? "Подготовка" : "Обработка"} · ${Math.round(job.progress * 100)}%`}
                      </span>
                    </div>
                  )}
                  <div className="source-controls">
                    {source.kind === "rtsp" && (
                      <button
                        className="button secondary"
                        disabled={
                          job?.status === "running" && job.kind === "probe"
                        }
                        onClick={() =>
                          quiet(() =>
                            run(
                              () => post(`/sources/${source.id}/probe`),
                              "Подключение запущено",
                            ),
                          )
                        }
                      >
                        <RefreshCw size={16} />
                        Подключить снова
                      </button>
                    )}
                    <button
                      className="button secondary"
                      disabled={!source.preview_url || !monitor?.plan}
                      onClick={() => setZoneSource(source)}
                    >
                      <ScanLine size={16} />
                      Редактировать зоны
                    </button>
                    {job ? (
                      <button
                        className="button secondary"
                        onClick={() =>
                          quiet(() =>
                            run(
                              () => post(`/sources/${source.id}/stop`),
                              "Обработка остановлена",
                            ),
                          )
                        }
                      >
                        <Pause size={16} />
                        Стоп
                      </button>
                    ) : (
                      <button
                        className="button primary"
                        title={
                          !monitor?.model.ready
                            ? "Подключите модель детектора"
                            : undefined
                        }
                        disabled={!monitor?.model.ready || !source.preview_url}
                        onClick={() =>
                          quiet(() =>
                            run(
                              () => post(`/sources/${source.id}/analyze`),
                              "Анализ запущен",
                            ),
                          )
                        }
                      >
                        <Play size={16} />
                        Анализ
                      </button>
                    )}
                  </div>
                  {source.status === "completed" && (
                    <button
                      type="button"
                      className="button secondary source-results-button"
                      onClick={() => onOpenStatistics(source.id)}
                    >
                      <Activity size={16} />
                      Открыть результаты и статистику
                    </button>
                  )}
                  <details className="source-details">
                    <summary>Параметры источника</summary>
                    <label>
                      Интервал обработки, секунды
                      <input
                        type="number"
                        min="1"
                        max="3600"
                        defaultValue={source.sample_seconds}
                        disabled={!!job}
                        onBlur={(e) => {
                          const n = Number(e.target.value);
                          if (n !== source.sample_seconds)
                            quiet(() =>
                              run(() =>
                                api(`/sources/${source.id}`, {
                                  method: "PATCH",
                                  body: JSON.stringify({ sample_seconds: n }),
                                }),
                              ),
                            );
                        }}
                      />
                    </label>
                    <label>
                      Начало записи, МСК
                      <input
                        type="datetime-local"
                        disabled={!!job || source.kind === "rtsp"}
                        defaultValue={inputDate(source.capture_start || "")}
                        onBlur={(e) => {
                          const next = e.target.value
                            ? isoDate(e.target.value)
                            : null;
                          if (next !== source.capture_start)
                            quiet(() =>
                              run(
                                () =>
                                  api(`/sources/${source.id}`, {
                                    method: "PATCH",
                                    body: JSON.stringify({
                                      sample_seconds: source.sample_seconds,
                                      capture_start: next,
                                    }),
                                  }),
                                "Время применяется к следующему анализу",
                              ),
                            );
                        }}
                      />
                    </label>
                    <button
                      className="text-button"
                      disabled={!!job}
                      onClick={() =>
                        quiet(() =>
                          run(
                            () => post(`/sources/${source.id}/probe`),
                            "Проверка источника запущена",
                          ),
                        )
                      }
                    >
                      <RefreshCw size={14} />
                      Проверить подключение
                    </button>
                  </details>
                </div>
              </section>
            );
          })}
        </div>
      ) : (
        <section className="panel">
          <div className="panel-help-heading">
            <h2>Источники наблюдения</h2>
            <InfoButton title="Источники наблюдения">
              <p>
                Добавьте видео, снимок или сетевую камеру. Для сопоставления с
                планом источнику нужны время записи и привязанный этап.
              </p>
            </InfoButton>
          </div>
          <Empty
            icon={Camera}
            title="Подключите первый источник"
            text="Начните с видео стройки или снимка. Для сетевой камеры разрешите её адрес на сервере."
            action={
              <button
                className="button primary"
                onClick={() => setAdding(true)}
              >
                <Plus size={17} />
                Добавить источник
              </button>
            }
          />
        </section>
      )}
      {adding && (
        <SourceCreateModal
          project={project}
          run={run}
          onClose={() => setAdding(false)}
          onCreated={() => setAdding(false)}
        />
      )}
      {zoneSource && monitor?.plan && (
        <ZoneEditor
          source={zoneSource}
          works={monitor.plan.works}
          workStates={monitor.work_states}
          onClose={() => setZoneSource(null)}
          onSave={(data) =>
            run(
              () => post(`/sources/${zoneSource.id}/bindings`, data),
              "Новая версия зон сохранена",
            )
          }
        />
      )}
      {editSource && (
        <SourceEditorModal
          source={editSource}
          onClose={() => setEditSource(null)}
          onSave={async (data) => {
            await run(
              () =>
                api(`/sources/${editSource.id}`, {
                  method: "PATCH",
                  body: JSON.stringify(data),
                }),
              "Источник обновлён",
            );
            setEditSource(null);
          }}
        />
      )}
      {deleteSource && (
        <Modal
          title={`Удалить источник «${deleteSource.name}»`}
          onClose={() => setDeleteSource(null)}
        >
          <p>
            Будут удалены источник, его привязки, наблюдения, сигналы и
            сохранённые кадры. Это действие нельзя отменить.
          </p>
          <div className="form-actions">
            <button
              className="button secondary"
              onClick={() => setDeleteSource(null)}
            >
              Отмена
            </button>
            <button
              className="button destructive"
              onClick={() =>
                quiet(() =>
                  run(async () => {
                    await api(`/sources/${deleteSource.id}`, {
                      method: "DELETE",
                    });
                    setDeleteSource(null);
                  }, "Источник удалён"),
                )
              }
            >
              <Trash2 size={16} />
              Удалить источник
            </button>
          </div>
        </Modal>
      )}
      {viewSource && (
        <Modal title={viewSource.name} wide onClose={() => setViewSource(null)}>
          {viewSource.kind === "video" ? (
            <video
              controls
              className="video-player"
              src={`/api/v1/sources/${viewSource.id}/content`}
            />
          ) : (
            <img
              className="evidence-image"
              src={viewSource.preview_url || ""}
              alt={viewSource.name}
            />
          )}
          <p className="hint">
            {date(viewSource.capture_start)} · оригинальный источник. Разметка
            детектора показывается в мониторинге после анализа.
          </p>
        </Modal>
      )}
    </>
  );
}
function sourceStatusText(source: Source) {
  const phase = source.processing_state || source.status;
  if (phase === "reconnecting" && source.retry_at) {
    return `Повтор подключения в ${date(source.retry_at)} · попытка ${source.connection_attempt || 1}`;
  }
  return sourceStatusName[phase] || stateName[source.status] || source.status;
}

function SourceEditorModal({
  source,
  onClose,
  onSave,
}: {
  source: Source;
  onClose: () => void;
  onSave: (data: {
    name: string;
    sample_seconds: number;
    capture_start?: string | null;
    uri?: string;
    demo_loop_enabled?: boolean;
    demo_loop_duration_seconds?: number;
  }) => Promise<void>;
}) {
  const initialDemoDuration = demoDurationParts(
    source.metadata_json.demo_loop?.duration_seconds,
  );
  const [name, setName] = useState(source.name),
    [interval, setIntervalValue] = useState(source.sample_seconds),
    [start, setStart] = useState(inputDate(source.capture_start || "")),
    [uri, setUri] = useState(""),
    [savedUri, setSavedUri] = useState(""),
    [uriLoading, setUriLoading] = useState(source.kind === "rtsp"),
    [uriError, setUriError] = useState(""),
    [demoLoop, setDemoLoop] = useState(
      !!source.metadata_json.demo_loop?.enabled,
    ),
    [demoLoopHours, setDemoLoopHours] = useState(initialDemoDuration.hours),
    [demoLoopMinutes, setDemoLoopMinutes] = useState(
      initialDemoDuration.minutes,
    ),
    [saving, setSaving] = useState(false),
    [saveError, setSaveError] = useState("");
  useEffect(() => {
    if (source.kind !== "rtsp") return;
    let active = true;
    api<{ uri: string }>(`/sources/${source.id}/connection`, {
      cache: "no-store",
    })
      .then((data) => {
        if (!active) return;
        setUri(data.uri);
        setSavedUri(data.uri);
      })
      .catch((error: Error) => {
        if (active) setUriError(error.message);
      })
      .finally(() => {
        if (active) setUriLoading(false);
      });
    return () => {
      active = false;
    };
  }, [source.id, source.kind]);
  return (
    <Modal title="Изменить источник" onClose={onClose}>
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (saving) return;
          setSaving(true);
          setSaveError("");
          try {
            await onSave({
              name: name.trim(),
              sample_seconds: interval,
              ...(source.kind === "rtsp"
                ? uri.trim() && uri.trim() !== savedUri
                  ? { uri: uri.trim() }
                  : {}
                : start !== inputDate(source.capture_start || "")
                  ? { capture_start: start ? isoDate(start) : null }
                  : {}),
              ...(source.kind === "video" &&
              (demoLoop !== !!source.metadata_json.demo_loop?.enabled ||
                (demoLoop &&
                  demoDurationSeconds(demoLoopHours, demoLoopMinutes) !==
                    source.metadata_json.demo_loop?.duration_seconds))
                ? {
                    demo_loop_enabled: demoLoop,
                    ...(demoLoop
                      ? {
                          demo_loop_duration_seconds: demoDurationSeconds(
                            demoLoopHours,
                            demoLoopMinutes,
                          ),
                        }
                      : {}),
                  }
                : {}),
            });
          } catch (error) {
            setSaveError((error as Error).message);
          } finally {
            setSaving(false);
          }
        }}
      >
        <label>
          Название
          <input
            required
            maxLength={200}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        {source.kind === "rtsp" ? (
          <label>
            Адрес RTSP или HTTPS
            <input
              type="text"
              value={uri}
              onChange={(event) => setUri(event.target.value)}
              disabled={uriLoading || saving}
              required
              placeholder={
                uriLoading ? "Загружаем адрес…" : "rtsp:// или https://"
              }
              autoComplete="off"
              spellCheck={false}
            />
            {uriError && (
              <span className="inline-warning" role="alert">
                {uriError}
              </span>
            )}
            <span className="hint">
              При изменении адреса подключение будет проверено заново.
            </span>
          </label>
        ) : (
          <div className="stable-datetime-field">
            <span>Дата и время начала записи, МСК</span>
            <StableDateTimeInput
              required={source.kind === "video" && demoLoop}
              value={start}
              onChange={setStart}
            />
          </div>
        )}
        {source.kind === "video" && (
          <div className="demo-loop-box">
            <label className="checkbox">
              <input
                type="checkbox"
                checked={demoLoop}
                onChange={(event) => setDemoLoop(event.target.checked)}
              />
              <strong>Циклически повторять видео</strong>
            </label>
            {demoLoop && (
              <>
                <p className="demo-loop-warning">
                  Тестовый демо-режим: видео будет циклически повторяться.
                  Результаты не являются непрерывной реальной съёмкой.
                </p>
                <div className="demo-duration-fields">
                  <label>
                    Часы
                    <input
                      type="number"
                      min="0"
                      step="1"
                      required
                      value={demoLoopHours}
                      onChange={(event) =>
                        setDemoLoopHours(Number(event.target.value))
                      }
                    />
                  </label>
                  <label>
                    Минуты
                    <input
                      type="number"
                      min={demoLoopHours === 0 ? "1" : "0"}
                      max="59"
                      step="1"
                      required
                      value={demoLoopMinutes}
                      onChange={(event) =>
                        setDemoLoopMinutes(Number(event.target.value))
                      }
                    />
                  </label>
                </div>
              </>
            )}
            <p className="hint">
              Если длительность выходит за границу привязанного этапа, анализ
              автоматически остановится в момент его окончания.
            </p>
          </div>
        )}
        <label>
          Обрабатывать один кадр каждые, секунд
          <input
            type="number"
            min="1"
            max="3600"
            step="1"
            required
            value={interval}
            onChange={(event) => setIntervalValue(Number(event.target.value))}
          />
        </label>
        {saveError && (
          <p role="alert" className="banner danger">
            {saveError}
          </p>
        )}
        <div className="form-actions">
          <button
            type="button"
            className="button secondary"
            onClick={onClose}
            disabled={saving}
          >
            Отмена
          </button>
          <button className="button primary" disabled={saving || uriLoading}>
            <Check size={16} />
            {saving ? "Сохраняем…" : "Сохранить"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
const polygonColors = [
  "#27836b",
  "#d66a28",
  "#6366d9",
  "#c43e77",
  "#168bad",
  "#a5780d",
  "#893eaa",
  "#51831d",
  "#bd4444",
  "#4175bb",
  "#397b76",
  "#ac612b",
];
const fullFramePolygon: [number, number][] = [
  [0, 0],
  [1, 0],
  [1, 1],
  [0, 1],
];
const isFullFramePolygon = (polygon: [number, number][]) =>
  polygon.length === 4 &&
  fullFramePolygon.every(
    (point, index) =>
      polygon[index]?.[0] === point[0] && polygon[index]?.[1] === point[1],
  );
function ZoneEditor({
  source,
  works,
  workStates,
  onClose,
  onSave,
}: {
  source: Source;
  works: Work[];
  workStates: Record<string, string>;
  onClose: () => void;
  onSave: (b: {
    expected_revision: number;
    regions: Region[];
  }) => Promise<void>;
}) {
  const { data, error } = useData<Binding>(`/sources/${source.id}/bindings`);
  const [loadedRevision, setLoadedRevision] = useState<number | null>(null);
  const [regions, setRegions] = useState<Region[]>([]),
    [selected, setSelected] = useState(""),
    [drawing, setDrawing] = useState(false),
    [points, setPoints] = useState<[number, number][]>([]),
    [replacedDefault, setReplacedDefault] = useState<Region | null>(null),
    [saving, setSaving] = useState(false),
    [saveError, setSaveError] = useState("");
  const upcomingWorks = works
    .filter((work) => workStates[work.id] !== "completed")
    .sort(compareWorkByCodeThenDate);
  const completedWorks = works
    .filter((work) => workStates[work.id] === "completed")
    .sort(compareWorkByCodeThenDate);
  useEffect(() => {
    if (data) {
      setLoadedRevision(data.revision);
      setRegions(
        data.regions.map((r, i) => ({
          ...r,
          color: r.color || polygonColors[i % polygonColors.length],
          visible: r.visible !== false,
        })),
      );
      const upcomingIds = new Set(upcomingWorks.map((work) => work.id));
      setSelected(
        data.regions.find((region) =>
          region.work_ids.some((id) => upcomingIds.has(id)),
        )?.id ||
          data.regions[0]?.id ||
          "",
      );
    }
  }, [data]);
  const region = regions.find((r) => r.id === selected);
  const update = (patch: Partial<Region>) =>
    setRegions((v) =>
      v.map((r) => (r.id === selected ? { ...r, ...patch } : r)),
    );
  const begin = (work: Work) => {
    const defaultRegion = regions.find(
      (item) =>
        item.work_ids.includes(work.id) && isFullFramePolygon(item.polygon),
    );
    const created: Region = {
      id: crypto.randomUUID(),
      name: work.title,
      work_ids: [work.id],
      polygon: [],
      color:
        defaultRegion?.color ||
        polygonColors[regions.length % polygonColors.length],
      visible: true,
      primary: true,
      visibility_confirmed: true,
    };
    setRegions((value) => [
      ...value.flatMap((item) => {
        if (item.id !== defaultRegion?.id) return [item];
        const workIds = item.work_ids.filter((id) => id !== work.id);
        return workIds.length ? [{ ...item, work_ids: workIds }] : [];
      }),
      created,
    ]);
    setSelected(created.id);
    setPoints([]);
    setReplacedDefault(defaultRegion || null);
    setDrawing(true);
  };
  const bindFullFrame = (work: Work) => {
    const created: Region = {
      id: crypto.randomUUID(),
      name: `${work.title} · весь кадр`,
      work_ids: [work.id],
      polygon: fullFramePolygon.map(
        (point) => [point[0], point[1]] as [number, number],
      ),
      color: polygonColors[regions.length % polygonColors.length],
      visible: true,
      primary: true,
      visibility_confirmed: true,
    };
    setRegions((value) => [...value, created]);
    setSelected(created.id);
  };
  const unbindWork = (workId: string) => {
    setRegions((value) =>
      value.flatMap((item) => {
        if (!item.work_ids.includes(workId)) return [item];
        const workIds = item.work_ids.filter((id) => id !== workId);
        return workIds.length ? [{ ...item, work_ids: workIds }] : [];
      }),
    );
    if (region?.work_ids.includes(workId)) setSelected("");
  };
  const cancel = () => {
    setRegions((value) => {
      const remaining = value.filter(
        (item) => item.id !== selected || item.polygon.length >= 3,
      );
      if (!replacedDefault) return remaining;
      const existing = remaining.find((item) => item.id === replacedDefault.id);
      return existing
        ? remaining.map((item) =>
            item.id === replacedDefault.id
              ? {
                  ...item,
                  work_ids: Array.from(
                    new Set([...item.work_ids, ...replacedDefault.work_ids]),
                  ),
                }
              : item,
          )
        : [...remaining, replacedDefault];
    });
    setDrawing(false);
    setPoints([]);
    setReplacedDefault(null);
  };
  const choose = (id: string) => {
    if (!drawing) setSelected(id);
  };
  const renderStagePolygon = (work: Work) => {
    const assigned = regions.filter((r) => r.work_ids.includes(work.id));
    const status = workStates[work.id] || "planned";
    return (
      <div
        className={
          "stage-polygon" +
          (assigned.some((r) => r.id === selected) ? " active" : "")
        }
        key={work.id}
      >
        <div className="stage-polygon-heading">
          <label className="checkbox">
            <input
              type="checkbox"
              aria-label={`Привязать этап ${work.code} к источнику`}
              checked={assigned.length > 0}
              disabled={drawing}
              onChange={(e) =>
                e.target.checked ? bindFullFrame(work) : unbindWork(work.id)
              }
            />
            <span>
              <span className={`stage-state ${status}`}>
                {stateName[status] || status}
              </span>
              <code>{work.code}</code>
              {work.title}
            </span>
          </label>
          <button
            className="text-button"
            disabled={drawing}
            onClick={() => begin(work)}
          >
            <Plus size={15} />
            {assigned.length ? "Добавить полигон" : "Нарисовать полигон"}
          </button>
        </div>
        {assigned.map((r) => (
          <div className="polygon-row" key={r.id}>
            <button
              className={
                "polygon-label" + (r.id === selected ? " selected" : "")
              }
              disabled={drawing}
              onClick={() => choose(r.id)}
            >
              <i style={{ background: r.color }} />
              {r.name}
              <span>
                {isFullFramePolygon(r.polygon) ? "Весь кадр" : "Полигон"}
              </span>
            </button>
            <label className="polygon-visibility">
              <input
                type="checkbox"
                checked={r.visible !== false}
                disabled={drawing}
                onChange={(event) =>
                  setRegions((value) =>
                    value.map((item) =>
                      item.id === r.id
                        ? { ...item, visible: event.target.checked }
                        : item,
                    ),
                  )
                }
              />
              Показывать
            </label>
          </div>
        ))}
      </div>
    );
  };
  if (!data || loadedRevision !== data.revision)
    return (
      <Modal
        title={`Редактировать зоны · ${source.name}`}
        wide
        onClose={onClose}
      >
        {error ? (
          <div className="banner danger" role="alert">
            {error}
          </div>
        ) : (
          <p role="status">Загрузка привязок...</p>
        )}
      </Modal>
    );
  return (
    <Modal title={`Редактировать зоны · ${source.name}`} wide onClose={onClose}>
      {error && <div className="banner danger">{error}</div>}
      <div className="zone-layout">
        <div className="zone-preview">
          <div
            className={`zone-canvas ${drawing ? "is-drawing" : ""}`}
            role="img"
            aria-label="Кадр для разметки полигонов"
            style={{
              aspectRatio: `${source.metadata_json.width || 16}/${source.metadata_json.height || 9}`,
            }}
            onClick={(e) => {
              if (!drawing) return;
              const b = e.currentTarget.getBoundingClientRect();
              setPoints((v) => [
                ...v,
                [
                  Math.min(1, Math.max(0, (e.clientX - b.left) / b.width)),
                  Math.min(1, Math.max(0, (e.clientY - b.top) / b.height)),
                ],
              ]);
            }}
          >
            <img src={source.preview_url!} alt="Кадр для выделения зоны" />
            <svg
              viewBox="0 0 1000 1000"
              preserveAspectRatio="none"
              aria-hidden="true"
            >
              {regions
                .filter(
                  (r) =>
                    r.visible !== false &&
                    r.polygon.length >= 3 &&
                    !(drawing && r.id === selected),
                )
                .map((r) => (
                  <polygon
                    key={r.id}
                    data-region-id={r.id}
                    points={r.polygon
                      .map((p) => `${p[0] * 1000},${p[1] * 1000}`)
                      .join(" ")}
                    fill={`${r.color}33`}
                    stroke={r.color}
                    strokeWidth={r.id === selected ? 5 : 3}
                    vectorEffect="non-scaling-stroke"
                  />
                ))}
              {drawing && (
                <>
                  <polyline
                    points={points
                      .map((p) => `${p[0] * 1000},${p[1] * 1000}`)
                      .join(" ")}
                    stroke={region?.color || "#ffffff"}
                    fill="none"
                    strokeWidth="3"
                    vectorEffect="non-scaling-stroke"
                  />
                  {points.map((p, i) => (
                    <g key={i}>
                      <circle
                        cx={p[0] * 1000}
                        cy={p[1] * 1000}
                        r="6"
                        fill="white"
                        stroke={region?.color}
                        strokeWidth="2"
                      />
                      <text
                        x={p[0] * 1000 + 10}
                        y={p[1] * 1000 - 10}
                        fill="white"
                        fontSize="20"
                      >
                        {i + 1}
                      </text>
                    </g>
                  ))}
                </>
              )}
            </svg>
          </div>
          <p className="hint">
            {drawing
              ? `Точек: ${points.length}. Добавьте минимум 3 точки на изображении и завершите полигон.`
              : "Галочка привязывает этап ко всему кадру. Отдельный полигон можно добавить при необходимости."}
          </p>
          <p className="hint">
            По умолчанию подсчёт выполняется по всему кадру. Добавленные
            полигоны заменяют область всего кадра для выбранного этапа.
          </p>
        </div>
        <div className="zone-settings">
          <h3>Этапы и полигоны</h3>
          <p className="hint stage-priority-hint">
            Этапы отсортированы по индексу работы, затем по дате начала.
          </p>
          <div className="stage-polygons">
            {upcomingWorks.map(renderStagePolygon)}
            {completedWorks.length > 0 && (
              <details className="completed-stage-group">
                <summary>
                  <span>Завершённые этапы</span>
                  <b>{completedWorks.length}</b>
                </summary>
                <div>{completedWorks.map(renderStagePolygon)}</div>
              </details>
            )}
          </div>
          {region && (
            <div className="polygon-editor">
              <div className="polygon-name">
                <label>
                  Название полигона
                  <input
                    value={region.name}
                    onChange={(e) => update({ name: e.target.value })}
                  />
                </label>
                <label>
                  Цвет
                  <input
                    type="color"
                    aria-label="Цвет полигона"
                    value={region.color}
                    onChange={(e) => update({ color: e.target.value })}
                  />
                </label>
              </div>
              <div className="zone-toolbar">
                {drawing ? (
                  <>
                    <button
                      className="button secondary"
                      disabled={!points.length}
                      onClick={() => setPoints((v) => v.slice(0, -1))}
                    >
                      Отменить последнюю точку
                    </button>
                    <button
                      className="button primary"
                      disabled={points.length < 3}
                      onClick={() => {
                        update({ polygon: points, visible: true });
                        setDrawing(false);
                        setPoints([]);
                        setReplacedDefault(null);
                      }}
                    >
                      <Check size={16} />
                      Завершить полигон
                    </button>
                    <button className="text-button" onClick={cancel}>
                      Отмена рисования
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      className="button secondary"
                      onClick={() => {
                        update({ visible: true });
                        setPoints([]);
                        setDrawing(true);
                      }}
                    >
                      <ScanLine size={16} />
                      Перерисовать полигон
                    </button>
                    <button
                      className="text-button destructive"
                      onClick={() => {
                        setRegions((v) => v.filter((r) => r.id !== selected));
                        setSelected("");
                      }}
                    >
                      <Trash2 size={15} />
                      Удалить полигон
                    </button>
                  </>
                )}
              </div>
            </div>
          )}
          {regions
            .filter(
              (r) => !r.work_ids.some((id) => works.some((w) => w.id === id)),
            )
            .map((r) => (
              <div className="inline-warning" key={r.id}>
                <span>Этап полигона «{r.name}» отсутствует в плане.</span>
                <button
                  className="text-button destructive"
                  disabled={drawing}
                  onClick={() =>
                    setRegions((v) => v.filter((x) => x.id !== r.id))
                  }
                >
                  Удалить полигон
                </button>
              </div>
            ))}
        </div>
      </div>
      {saveError && (
        <div role="alert" className="banner danger zone-save-error">
          <TriangleAlert size={18} />
          <span>{saveError}</span>
        </div>
      )}
      <div className="form-actions">
        <span className="hint">Изменения применятся к следующему анализу.</span>
        <button
          className="button primary"
          disabled={
            !data ||
            saving ||
            drawing ||
            regions.some(
              (r) =>
                !r.name.trim() ||
                r.polygon.length < 3 ||
                r.work_ids.some((id) => !works.some((w) => w.id === id)),
            )
          }
          onClick={async () => {
            setSaving(true);
            setSaveError("");
            try {
              await onSave({
                expected_revision: data!.revision,
                regions: regions.map((r) => ({
                  ...r,
                  primary: true,
                  visibility_confirmed: true,
                })),
              });
              onClose();
            } catch (error) {
              setSaveError((error as Error).message);
            } finally {
              setSaving(false);
            }
          }}
        >
          {saving ? "Сохраняем…" : "Сохранить привязки"}
        </button>
      </div>
    </Modal>
  );
}

function AlertsPage({
  catalog,
  project,
  alerts,
  run,
  quiet,
}: {
  catalog: Catalog;
  project: Project;
  alerts: Alert[];
  run: Run;
  quiet: Quiet;
}) {
  const [filter, setFilter] = useState("all"),
    [selected, setSelected] = useState<Alert | null>(null),
    [bulkBusy, setBulkBusy] = useState(false),
    [bulkError, setBulkError] = useState("");
  const filtered = alerts.filter(
    (a) =>
      filter === "all" ||
      (filter === "critical"
        ? a.severity === "critical"
        : filter === "open"
          ? a.status === "open" && !a.reviewed
          : a.reviewed),
  );
  const pendingFiltered = filtered.filter(
    (alert) => alert.status === "open" && !alert.reviewed,
  );
  const bulkReview = (decision: "confirmed" | "dismissed") => {
    if (!pendingFiltered.length || bulkBusy) return;
    const label = decision === "confirmed" ? "Принять" : "Отклонить";
    if (
      !window.confirm(
        `${label} ${pendingFiltered.length} открытых сигналов в текущем фильтре?`,
      )
    )
      return;
    quiet(async () => {
      setBulkBusy(true);
      setBulkError("");
      let completed = 0;
      try {
        for (const alert of pendingFiltered) {
          await post(`/alerts/${alert.id}/reviews`, { decision, note: "" });
          completed++;
        }
      } catch (error) {
        setBulkError(
          `Проверено ${completed} из ${pendingFiltered.length}: ${(error as Error).message}`,
        );
      } finally {
        if (completed)
          await run(async () => {}, `Проверено сигналов: ${completed}`);
        setBulkBusy(false);
      }
    });
  };
  return (
    <>
      <PageHead
        eyebrow="КОНТРОЛЬ ОТКЛОНЕНИЙ"
        title="Сигналы и проверки"
        description="Каждый сигнал связан с кадром, зоной и версией плана."
      />
      <div className="filter-row">
        {[
          ["all", "Все сигналы"],
          ["open", "Открытые"],
          ["critical", "Критические"],
          ["reviewed", "Проверенные"],
        ].map(([key, label]) => (
          <button
            key={key}
            className={filter === key ? "active" : ""}
            onClick={() => setFilter(key)}
          >
            {label}
          </button>
        ))}
        <div className="filter-bulk-actions">
          <button
            type="button"
            className="button secondary"
            disabled={bulkBusy || !pendingFiltered.length}
            onClick={() => bulkReview("confirmed")}
          >
            Принять все
          </button>
          <button
            type="button"
            className="button secondary"
            disabled={bulkBusy || !pendingFiltered.length}
            onClick={() => bulkReview("dismissed")}
          >
            Отклонить все
          </button>
        </div>
        <span>{filtered.length} записей</span>
        <InfoButton title="Сигналы и проверки">
          <p>
            Сигнал связан с кадром, этапом и версией плана. Критический уровень:
            дефицит более{" "}
            {Math.round(project.settings.critical_missing_ratio * 100)}% в
            течение {duration(project.settings.critical_after_seconds)}.
            Отсутствие подтверждается после{" "}
            {duration(project.settings.absence_confirm_seconds)} непрерывного
            пропуска.
          </p>
        </InfoButton>
      </div>
      {bulkError && (
        <p className="banner danger" role="alert">
          {bulkError}
        </p>
      )}
      <section className="panel">
        {filtered.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Уровень</th>
                  <th>Отклонение</th>
                  <th>Этап</th>
                  <th>Наблюдаемая длительность</th>
                  <th>Состояние</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {filtered.map((a) => (
                  <tr key={a.id}>
                    <td>
                      <Badge tone={a.severity === "critical" ? "red" : "amber"}>
                        {a.severity === "critical" ? "Критический" : "Средний"}
                      </Badge>
                    </td>
                    <td>
                      <strong>{alertKindName(a.kind)}</strong>
                      <small>{a.details.region_name}</small>
                    </td>
                    <td>{a.details.title}</td>
                    <td>
                      {duration(a.duration_seconds)}
                      <small>{date(a.first_seen)}</small>
                    </td>
                    <td>
                      {stateName[a.status] || a.status}
                      {a.reviewed && <small>Проверен оператором</small>}
                    </td>
                    <td>
                      <button
                        className="text-button"
                        onClick={() => {
                          setSelected(a);
                        }}
                      >
                        Проверить
                        <ArrowUpRight size={15} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            icon={ShieldCheck}
            title="Нет сигналов по выбранному фильтру"
            text="Отклонения появляются после обработки пригодных кадров и сопоставления с утверждённым планом."
          />
        )}
      </section>
      {selected && (
        <AlertReviewModal
          alert={selected}
          catalog={catalog}
          onClose={() => setSelected(null)}
          run={run}
          quiet={quiet}
        />
      )}
    </>
  );
}
const actionLabels: Record<string, string> = {
  "project.created": "Объект создан",
  "project.classes_extended": "Расширен состав техники",
  "plan.draft_created": "План сохранён (история)",
  "plan.created": "План создан",
  "plan.approved": "План утверждён",
  "plan.deleted": "План удалён",
  "plan.shifted": "Сроки плана изменены",
  "work.status_changed": "Изменён статус этапа",
  "media.uploaded": "Загружено медиа",
  "source.created": "Добавлена камера",
  "source.deleted": "Удалён источник",
  "source.stopped": "Обработка остановлена",
  "source.configured": "Изменены параметры источника",
  "zones.updated": "Сохранены зоны и привязки",
  "analysis.started": "Запущен анализ",
  "settings.updated": "Изменены пороги правил",
  "alert.reviewed": "Проверено отклонение",
  "demo.generated": "Создан тестовый демо-сценарий",
};
function AuditPage({
  project,
  revision,
  catalog,
}: {
  project: Project;
  revision: number;
  catalog: Catalog;
}) {
  const { data, error } = useData<Audit[]>(
    `/projects/${project.id}/audit`,
    revision,
    10000,
  );
  return (
    <>
      <PageHead
        eyebrow="ИСТОРИЯ ОБЪЕКТА"
        title="Журнал событий"
        description="Решения оператора, изменения плана и параметры анализа."
      />
      {error && <div className="banner danger">{error}</div>}
      <section className="panel">
        <div className="panel-help-heading audit-help">
          <InfoButton title="Журнал событий">
            <p>
              Показаны последние 200 действий и решений. Записи журнала не
              изменяют исходные наблюдения.
            </p>
          </InfoButton>
        </div>
        {data?.length ? (
          <div className="timeline">
            {data.map((event) => (
              <div className="timeline-row" key={event.id}>
                <div className="timeline-icon">
                  <AuditIcon action={event.action} />
                </div>
                <div>
                  <h3>{actionLabels[event.action] || event.action}</h3>
                  <p>
                    {date(event.created_at)} ·{" "}
                    {event.actor === "demo-generator"
                      ? "Демо-генератор"
                      : "Оператор"}
                  </p>
                  <AuditDetails event={event} catalog={catalog} />
                </div>
                <code>{event.entity_id.slice(0, 8)}</code>
              </div>
            ))}
          </div>
        ) : (
          <Empty
            icon={History}
            title="Журнал пока пуст"
            text="Действия с планом, камерами и алертами будут сохранены здесь."
          />
        )}
      </section>
    </>
  );
}
function ReportsPage({
  project,
  monitor,
}: {
  project: Project;
  monitor: Monitor | null;
}) {
  const [from, setFrom] = useState(() => inputDate(new Date().toISOString())),
    [to, setTo] = useState(() => inputDate(new Date().toISOString()));
  const seededPlan = useRef<string | null>(null);
  const editedPeriod = useRef(false);
  useEffect(() => {
    const plan = monitor?.plan;
    if (!plan || seededPlan.current === plan.id || !plan.works.length) return;
    const first = [...plan.works].sort(
      (a, b) => Date.parse(a.starts_at) - Date.parse(b.starts_at),
    )[0];
    if (!editedPeriod.current) setFrom(inputDate(first.starts_at));
    seededPlan.current = plan.id;
  }, [monitor?.plan]);
  const query = new URLSearchParams();
  if (from) query.set("from", isoDate(from));
  if (to) query.set("to", isoDate(to));
  const periodError =
    from && to && Date.parse(isoDate(from)) >= Date.parse(isoDate(to))
      ? "Конец периода должен быть позже начала"
      : "";
  const events = (monitor?.alerts || []).filter(
    (alert) =>
      alert.status !== "unknown" &&
      !alert.details.suppressed &&
      alert.details.confirmed !== false,
  );
  return (
    <>
      <PageHead
        eyebrow="АНАЛИТИКА И ЭКСПОРТ"
        title="Отчёты по объекту"
        description="Только интервалы подтверждённых значимых событий."
      />
      <section className="panel report-intro">
        <div>
          <div className="panel-help-heading">
            <h2>Выгрузка событий</h2>
            <InfoButton title="Выгрузка событий">
              <p>
                Экспорт содержит интервалы подтверждённых значимых событий. Если
                период не выбран, выгружается вся история.
              </p>
            </InfoButton>
          </div>
          <div className="report-period-fields">
            <StageDatePicker
              value={from}
              onChange={(value) => {
                editedPeriod.current = true;
                setFrom(value);
              }}
              label="Начало периода, МСК"
              part="начала периода"
            />
            <StageDatePicker
              value={to}
              onChange={(value) => {
                editedPeriod.current = true;
                setTo(value);
              }}
              label="Конец периода, МСК"
              part="конца периода"
            />
          </div>
          {periodError && (
            <p className="banner danger" role="alert">
              {periodError}
            </p>
          )}
          <div className="report-actions">
            <a
              className="button primary"
              href={
                periodError
                  ? undefined
                  : `/api/v1/projects/${project.id}/reports/xlsx?${query}`
              }
              aria-disabled={Boolean(periodError)}
            >
              <ArrowDownToLine size={17} />
              Скачать Excel
            </a>
            <a
              className="button secondary"
              href={
                periodError
                  ? undefined
                  : `/api/v1/projects/${project.id}/reports/csv?${query}`
              }
              aria-disabled={Boolean(periodError)}
            >
              <ArrowDownToLine size={17} />
              Скачать CSV
            </a>
          </div>
        </div>
      </section>
      <section className="panel report-events">
        <div className="panel-help-heading">
          <h2>События</h2>
          <InfoButton title="События отчёта">
            <p>
              Показаны только подтверждённые интервалы, пересекающие выбранный
              период.
            </p>
          </InfoButton>
        </div>
        {events.filter(
          (event) =>
            (!from ||
              Date.parse(event.last_seen) >= Date.parse(isoDate(from))) &&
            (!to || Date.parse(event.first_seen) < Date.parse(isoDate(to))),
        ).length ? (
          <div className="report-event-list">
            {events
              .filter(
                (event) =>
                  (!from ||
                    Date.parse(event.last_seen) >= Date.parse(isoDate(from))) &&
                  (!to ||
                    Date.parse(event.first_seen) < Date.parse(isoDate(to))),
              )
              .map((event) => (
                <div key={event.id}>
                  <span>
                    {date(event.first_seen)} - {date(event.last_seen)}
                  </span>
                  <strong>{event.details.title}</strong>
                  <p>{event.details.message}</p>
                </div>
              ))}
          </div>
        ) : (
          <p className="hint">Подтверждённых событий за этот период нет.</p>
        )}
      </section>
    </>
  );
}
type ModelParameters = {
  confidence: number;
  iou: number;
  max_detections: number;
};
type ModelProfile = {
  product_mode_name: string;
  product: { parameters: ModelParameters; revision: number };
};
function ModelSettingsPanel() {
  const [profile, setProfile] = useState<ModelProfile | null>(null);
  const [draft, setDraft] = useState<ModelParameters | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    api<ModelProfile>("/detector/settings")
      .then((value) => {
        if (active) {
          setProfile(value);
          setDraft(value.product.parameters);
        }
      })
      .catch((failure) => active && setError((failure as Error).message));
    return () => {
      active = false;
    };
  }, []);
  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!profile || !draft || busy) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const updated = await api<ModelProfile>("/detector/settings", {
        method: "PUT",
        body: JSON.stringify({
          ...draft,
          expected_revision: profile.product.revision,
        }),
      });
      setProfile(updated);
      setDraft(updated.product.parameters);
      setMessage("Параметры модели сохранены для новых запусков анализа.");
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="panel settings-form model-settings-panel">
      <div className="panel-help-heading">
        <h2>Параметры модели</h2>
        <InfoButton title="Параметры модели">
          <p>
            Профиль фонового мониторинга действует для новых запусков всех
            объектов. Сохранённые наблюдения не пересчитываются.
          </p>
        </InfoButton>
      </div>
      {error && (
        <p className="banner danger" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="banner success" role="status">
          {message}
        </p>
      )}
      {draft ? (
        <form onSubmit={save} className="model-settings-fields">
          <label>
            Confidence, минимум уверенности
            <input
              type="number"
              min="0.01"
              max="1"
              step="0.01"
              required
              value={draft.confidence}
              onChange={(event) =>
                setDraft({ ...draft, confidence: Number(event.target.value) })
              }
            />
          </label>
          <label>
            NMS IoU, порог объединения рамок
            <input
              type="number"
              min="0.01"
              max="1"
              step="0.01"
              required
              value={draft.iou}
              onChange={(event) =>
                setDraft({ ...draft, iou: Number(event.target.value) })
              }
            />
          </label>
          <label>
            Максимум детекций на кадре
            <input
              type="number"
              min="1"
              max="1000"
              step="1"
              required
              value={draft.max_detections}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  max_detections: Number(event.target.value),
                })
              }
            />
          </label>
          <button className="button primary" type="submit" disabled={busy}>
            <Check size={17} />
            {busy ? "Сохраняем…" : "Сохранить параметры модели"}
          </button>
        </form>
      ) : !error ? (
        <p className="hint">Загружаем профиль модели…</p>
      ) : null}
    </section>
  );
}
function SettingsPage({
  project,
  catalog,
  run,
  quiet,
}: {
  project: Project;
  catalog: Catalog;
  run: Run;
  quiet: Quiet;
}) {
  const [settings, setSettings] = useState(project.settings);
  useEffect(() => setSettings(project.settings), [project.revision]);
  return (
    <>
      <PageHead
        eyebrow="ПРАВИЛА НАБЛЮДЕНИЯ"
        title="Настройки объекта"
        description="Пороги отклонений и ограничения интерпретации."
      />
      <form
        className="settings-columns"
        onSubmit={(e) => {
          e.preventDefault();
          quiet(() =>
            run(
              () =>
                api(`/projects/${project.id}/settings`, {
                  method: "PUT",
                  body: JSON.stringify({
                    ...settings,
                    expected_revision: project.revision,
                  }),
                }),
              "Настройки объекта сохранены",
            ),
          );
        }}
      >
        <section className="panel settings-form">
          <div className="panel-help-heading">
            <h2>Уровни предупреждений</h2>
            <InfoButton title="Уровни предупреждений">
              <p>
                Критический сигнал требует одновременно достаточной длительности
                и дефицита. Новые пороги применяются при следующем анализе.
              </p>
            </InfoButton>
          </div>
          <div>
            <label>
              Критический сигнал: отсутствие дольше, минут
              <input
                type="number"
                min="0.1"
                max="10080"
                step="0.1"
                value={settings.critical_after_seconds / 60}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    critical_after_seconds: Math.round(
                      Number(e.target.value) * 60,
                    ),
                  })
                }
              />
            </label>
            <label>
              Критический сигнал: дефицит техники больше, %
              <input
                type="number"
                min="0"
                max="100"
                value={settings.critical_missing_ratio * 100}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    critical_missing_ratio: Number(e.target.value) / 100,
                  })
                }
              />
            </label>
            <label>
              Сигнал среднего уровня: задержка, секунд
              <input
                type="number"
                min="0"
                max="604800"
                value={settings.warning_after_seconds}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    warning_after_seconds: Number(e.target.value),
                  })
                }
              />
            </label>
            <label>
              Подтверждение изменения числа машин, секунд
              <input
                type="number"
                min="0"
                max="3600"
                step="1"
                value={settings.count_change_confirm_seconds ?? 10}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    count_change_confirm_seconds: Number(e.target.value),
                  })
                }
              />
            </label>
            <label>
              Подтверждение отсутствия машины, секунд
              <input
                type="number"
                min="0"
                max="86400"
                step="1"
                value={settings.absence_confirm_seconds}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    absence_confirm_seconds: Number(e.target.value),
                  })
                }
              />
            </label>
            <label>
              Допустимый разрыв наблюдений, секунд
              <input
                type="number"
                min="1"
                max="3600"
                value={settings.max_gap_seconds}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    max_gap_seconds: Number(e.target.value),
                  })
                }
              />
            </label>
            <label>
              Связь трека при пропуске детекции, кадров
              <input
                type="number"
                min="1"
                max="30"
                step="1"
                value={settings.max_gap_frames ?? 3}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    max_gap_frames: Number(e.target.value),
                  })
                }
              />
            </label>
            <label>
              Удержание присутствия после потери детекции, кадров
              <input
                type="number"
                min="0"
                max="30"
                step="1"
                value={settings.presence_grace_frames}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    presence_grace_frames: Number(e.target.value),
                  })
                }
              />
            </label>
            <label>
              Интервал проверки предупреждений, секунд
              <input
                type="number"
                min="1"
                max="300"
                value={settings.monitor_interval_seconds}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    monitor_interval_seconds: Number(e.target.value),
                  })
                }
              />
            </label>
            <label>
              Простой подвижной техники: время видимой неподвижности, секунд
              <input
                type="number"
                min="10"
                max="3600"
                step="1"
                value={settings.idle_after_seconds ?? 300}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    idle_after_seconds: Number(e.target.value),
                  })
                }
              />
              <small>
                Для техники, работающей на месте, требуется не менее 120 секунд
                и вдвое больше указанного времени. После потери рамки движение
                проверяется ещё 10 секунд для подвижной и 30 секунд для такой
                техники.
              </small>
            </label>
            <label>
              Движение рабочих частей: порог изменения области, %
              <input
                type="number"
                min="0.1"
                max="10"
                step="0.1"
                value={(settings.motion_fraction_threshold ?? 0.008) * 100}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    motion_fraction_threshold: Number(e.target.value) / 100,
                  })
                }
              />
              <small>
                Меньше значение повышает чувствительность к движению рабочих
                частей.
              </small>
            </label>
            <button className="button primary">
              <Check size={17} />
              Сохранить настройки
            </button>
          </div>
        </section>
        <section className="panel settings-form">
          <div className="panel-help-heading">
            <h2>Классы техники</h2>
            <InfoButton title="Классы техники">
              <p>
                Тип «Работа на месте» учитывает движение рабочих частей при
                неподвижном корпусе. Определение сложных действий требует
                отдельной модели и размеченных видео.
              </p>
            </InfoButton>
          </div>
          <div className="class-list">
            {catalog.classes
              .filter((c) => project.class_ids.includes(c.id))
              .map((c) => (
                <div key={c.id}>
                  <span>{c.name}</span>
                  <select
                    aria-label={`Тип работы: ${c.name}`}
                    value={
                      settings.equipment_activity?.[String(c.id)] ||
                      c.activity_group
                    }
                    onChange={(e) =>
                      setSettings({
                        ...settings,
                        equipment_activity: {
                          ...settings.equipment_activity,
                          [String(c.id)]: e.target.value as
                            | "mobile"
                            | "stationary_capable",
                        },
                      })
                    }
                  >
                    <option value="mobile">Подвижная</option>
                    <option value="stationary_capable">Работа на месте</option>
                  </select>
                </div>
              ))}
          </div>
          <button type="submit" className="button primary">
            <Check size={17} />
            Сохранить типы техники
          </button>
        </section>
      </form>
      <ModelSettingsPanel />
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
