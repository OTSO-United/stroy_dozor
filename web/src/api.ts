export type Equipment = {
  id: number;
  name: string;
  activity_group: string;
  episodic: boolean;
};
export type Catalog = {
  version: string;
  classes: Equipment[];
  planning_class_ids: number[];
  project_types: { id: string; name: string }[];
  works: {
    code: string;
    title: string;
    parent_code: string;
    primary_class_ids: number[];
    project_type_ids: string[];
    resource_preset: {
      version: string;
      basis: string;
      is_section: boolean;
      counts: Record<string, number>;
      episodic_class_ids: number[];
      outside_class_ids: number[];
      explanation: string;
    };
  }[];
};
export type Settings = {
  equipment_activity?: Record<string, "mobile" | "stationary_capable">;
  critical_after_seconds: number;
  critical_missing_ratio: number;
  warning_after_seconds: number;
  absence_confirm_seconds: number;
  count_change_confirm_seconds: number;
  max_gap_seconds: number;
  max_gap_frames: number;
  presence_grace_frames: number;
  idle_after_seconds?: number;
  motion_fraction_threshold?: number;
  monitor_interval_seconds: number;
};
export type Project = {
  id: string;
  created_at: string;
  name: string;
  address: string;
  project_type_id: string | null;
  latitude: number | null;
  longitude: number | null;
  timezone: string;
  class_ids: number[];
  settings: Settings;
  revision: number;
  current_plan_id: string | null;
  summary?: {
    current_works: { id: string; code: string; title: string }[];
    sources: number;
    live_sources: number;
    open_alerts: number;
  };
};
export type Work = {
  id: string;
  code: string;
  title: string;
  starts_at: string;
  ends_at: string;
  resources: Record<string, number>;
};
export type Plan = {
  id: string;
  project_id: string;
  version: number;
  revision: number;
  status: string;
  works: Work[];
  parent_id: string | null;
  validation?: { errors: string[]; warnings: { message: string }[] };
};
export type Source = {
  id: string;
  name: string;
  kind: string;
  status: string;
  processing_state?: string;
  retry_at?: string | null;
  connection_attempt?: number | null;
  capture_start: string | null;
  sample_seconds: number;
  error: string | null;
  preview_url: string | null;
  metadata_json: {
    width?: number;
    height?: number;
    duration_seconds?: number;
    fps?: number;
    demo_loop?: {
      enabled: boolean;
      duration_seconds: number;
    };
  };
};
export type Region = {
  id: string;
  name: string;
  work_ids: string[];
  polygon: [number, number][];
  primary: boolean;
  visibility_confirmed: boolean;
  color?: string;
  visible?: boolean;
};
export type Binding = { revision: number; regions: Region[] };
export type Observation = {
  id: string;
  run_id?: string;
  captured_at: string | null;
  offset_seconds: number;
  detections: {
    class_id: string;
    confidence: number;
    bbox: number[];
    track_id?: number;
    motion?: string;
    activity?: "working" | "idle" | "unknown";
    activity_basis?:
      | "vehicle_motion"
      | "visible_mechanism_motion"
      | "visible_inactivity"
      | "recent_motion"
      | "poor_frame_quality"
      | "track_grace"
      | "visual_track_grace"
      | "idle_pending"
      | "idle_recheck"
      | "motion_pending"
      | "video_gap"
      | "stale_detection"
      | "camera_motion"
      | "camera_registration_uncertain"
      | "truncated_equipment"
      | "equipment_occluded"
      | "roi_too_small"
      | "roi_unusable"
      | "roi_changed"
      | "roi_motion_uncertain"
      | "activity_not_visually_observable"
      | "insufficient_video";
    observed?: boolean;
    presence_basis?: "detector" | "track_grace";
    missed_frames?: number;
  }[];
  quality: { usable: boolean };
  model: {
    name: string;
    sha256?: string;
    supported_classes?: string[];
    demo?: boolean;
    seed?: number;
  };
};
export type Result = {
  work_id?: string;
  title?: string;
  region_name?: string;
  readiness: string;
  message?: string;
  counts?: Record<string, number | null>;
  expected?: Record<string, number>;
  findings?: {
    kind: string;
    message: string;
    classes?: { class_id: string; count: number }[];
  }[];
  limitations?: string[];
};
export type Alert = {
  id: string;
  severity: string;
  kind: string;
  status: string;
  source_id: string;
  work_id: string;
  first_seen: string;
  last_seen: string;
  duration_seconds: number;
  reviewed: boolean;
  details: {
    review?: { decision: "confirmed" | "dismissed"; reviewed_at: string };
    confirmed?: boolean;
    suppressed?: boolean;
    message: string;
    title: string;
    region_name: string;
    counts: Record<string, number | null>;
    expected: Record<string, number>;
    classes?: { class_id: string; count: number }[];
    evidence_id: string;
    limitations: string[];
    plan_id: string;
    model_hash: string;
  };
};
export type Monitor = {
  dependency_warnings: { work_id: string; message: string }[];
  project: Project;
  plan: Plan | null;
  work_states: Record<string, string>;
  cards: {
    source: Source;
    binding: Binding | null;
    observation: Observation | null;
    assessment: { results: Result[] } | null;
    stale_plan: boolean;
    stale_context: boolean;
  }[];
  alerts: Alert[];
  model: { ready: boolean; reason?: string; name?: string };
  generated_at: string;
};
export type Job = {
  id: string;
  created_at: string;
  updated_at: string;
  source_id: string;
  kind: string;
  status: string;
  progress: number;
  error: string | null;
  works?: Pick<Work, "id" | "code" | "title">[];
  started_at?: string | null;
  results_ready?: boolean;
  continuous?: boolean;
  analysis_state?: string;
};
export type Audit = {
  id: string;
  action: string;
  entity_id: string;
  data: Record<string, unknown>;
  actor: string;
  created_at: string;
};
export type Point = {
  id: string;
  time: string | null;
  offset_seconds: number;
  counts: Record<string, number | null>;
  raw_counts?: Record<string, number | null>;
  presence?: Record<string, "present" | "absent" | "pending" | "unknown">;
  display_basis?: Record<string, "confirmed" | "sampled" | "unknown">;
  run_id: string;
  segment: number;
  activity: { working: number; idle: number; unknown: number } | null;
  display_activity?: { working: number | null; idle: number | null } | null;
  display_activity_by_class?: Record<
    string,
    { working: number | null; idle: number | null }
  >;
  continued_working_by_class?: Record<string, number>;
  activity_seconds_by_class?: Record<
    string,
    { working: number | null; idle: number | null }
  >;
  presence_seconds_by_class?: Record<
    string,
    { present: number; absent: number; pending: number; unknown: number }
  >;
  demo: boolean;
  sample_seconds?: number;
};

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  if (init.method && init.method !== "GET")
    headers.set("Idempotency-Key", crypto.randomUUID());
  const response = await fetch("/api/v1" + path, { ...init, headers });
  if (!response.ok) {
    const data = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    const d = data.detail;
    throw new Error(
      typeof d === "string" ? d : d?.message || JSON.stringify(d),
    );
  }
  return response.json();
}
export const post = <T>(path: string, data: unknown = {}) =>
  api<T>(path, { method: "POST", body: JSON.stringify(data) });
export const date = (value: string | null | undefined) =>
  value
    ? new Intl.DateTimeFormat("ru-RU", {
        dateStyle: "short",
        timeStyle: "short",
        timeZone: "Europe/Moscow",
      }).format(new Date(value))
    : "Время не задано";
export const duration = (seconds: number) =>
  seconds < 60
    ? `${Math.round(seconds)} с`
    : `${Math.floor(seconds / 3600) ? Math.floor(seconds / 3600) + " ч " : ""}${Math.floor((seconds % 3600) / 60)} мин`;
export const stateName: Record<string, string> = {
  queued: "В очереди",
  running: "Обработка",
  ready: "Готов к анализу",
  completed: "Завершено",
  failed: "Ошибка",
  cancelled: "Отменено",
  stopped: "Остановлен",
  succeeded: "Готово",
  planned: "Запланирован",
  in_progress: "В работе",
  open: "Открыт",
  unknown: "Нет подтверждения",
  interrupted: "Наблюдение прервано",
  resolved: "Устранён",
  no_plan: "Нет плана",
  no_binding: "Нет привязки",
  no_active_work: "Нет активных работ",
  unknown_time: "Не задано время",
  insufficient_evidence: "Недостаточно данных",
};
