import {
  Building2,
  CalendarDays,
  Camera,
  ClipboardCheck,
  FileSpreadsheet,
  History,
  MapPinned,
  Play,
  Settings2,
  Square,
  Trash2,
} from "lucide-react";
import { date, stateName, type Audit, type Catalog } from "./api";

export function AuditIcon({ action }: { action: string }) {
  const Icon = action.endsWith("deleted")
    ? Trash2
    : action.startsWith("project.")
      ? Building2
      : action.startsWith("plan.")
        ? FileSpreadsheet
        : action.startsWith("work.")
          ? CalendarDays
          : action.includes("binding") || action.startsWith("zones.")
            ? MapPinned
            : action.includes("started")
              ? Play
              : action.includes("stopped")
                ? Square
                : action.startsWith("source.")
                  ? Camera
                  : action.includes("settings")
                    ? Settings2
                    : action.startsWith("alert.")
                      ? ClipboardCheck
                      : History;
  return <Icon size={20} />;
}
const fields: Record<string, string> = {
  name: "Название",
  address: "Адрес",
  project_type_id: "Вид объекта",
  latitude: "Широта",
  longitude: "Долгота",
  critical_after_seconds: "Критическая длительность, с",
  critical_missing_ratio: "Критическая доля дефицита",
  warning_after_seconds: "Задержка предупреждения, с",
  absence_confirm_seconds: "Подтверждение отсутствия, с",
  count_change_confirm_seconds: "Сглаживание скачков количества, с",
  max_gap_seconds: "Допустимый разрыв временной шкалы, с",
  max_gap_frames: "Пропуск трека, кадров",
  presence_grace_frames: "Удержание присутствия, кадров",
  monitor_interval_seconds: "Период проверки, с",
  equipment_activity: "Типы техники",
  sample_seconds: "Шаг анализа, с",
  capture_start: "Начало записи",
};
function display(value: unknown, catalog: Catalog): string {
  if (value === null || value === undefined || value === "") return "Не задано";
  if (typeof value === "string")
    return (
      stateName[value] ||
      catalog.project_types.find((t) => t.id === value)?.name ||
      value
    );
  if (typeof value === "object")
    return Object.entries(value as Record<string, unknown>)
      .map(
        ([k, v]) =>
          `${catalog.classes.find((c) => String(c.id) === k)?.name || k}: ${v === "mobile" ? "Подвижная" : v === "stationary_capable" ? "Работа на месте" : display(v, catalog)}`,
      )
      .join(", ");
  return String(value);
}
export function AuditDetails({
  event,
  catalog,
}: {
  event: Audit;
  catalog: Catalog;
}) {
  const d = event.data;
  const before =
    d.before && typeof d.before === "object" && !Array.isArray(d.before)
      ? (d.before as Record<string, unknown>)
      : null;
  const after =
    d.after && typeof d.after === "object" && !Array.isArray(d.after)
      ? (d.after as Record<string, unknown>)
      : null;
  const changes = Array.isArray(d.changes)
    ? (d.changes as {
        code: string;
        title: string;
        before: string[];
        after: string[];
      }[])
    : [];
  return (
    <div className="audit-summary">
      {Boolean(d.title || d.name) && (
        <p>
          <strong>{String(d.title || d.name)}</strong>
          {d.code ? ` · ${d.code}` : ""}
        </p>
      )}
      {typeof d.version === "number" && (
        <p>
          Версия плана: {d.version}
          {typeof d.work_count === "number" ? ` · этапов: ${d.work_count}` : ""}
        </p>
      )}
      {Boolean(d.project_type_id) && (
        <p>Вид объекта: {display(d.project_type_id, catalog)}</p>
      )}
      {Boolean(d.address) && <p>Адрес: {String(d.address)}</p>}
      {event.action === "work.status_changed" && (
        <p>
          {display(d.before, catalog)} → {display(d.after, catalog)}
        </p>
      )}
      {event.action === "project.classes_extended" &&
        Array.isArray(d.after) && (
          <p>
            Добавлены классы техники:{" "}
            {d.after
              .filter(
                (id) => !Array.isArray(d.before) || !d.before.includes(id),
              )
              .map(
                (id) =>
                  catalog.classes.find((c) => c.id === id)?.name || String(id),
              )
              .join(", ")}
          </p>
        )}
      {before && after && (
        <ul>
          {Object.entries(after)
            .filter(
              ([key, v]) => JSON.stringify(v) !== JSON.stringify(before[key]),
            )
            .map(([key, v]) => (
              <li key={key}>
                {fields[key] || key}: {display(before[key], catalog)} →{" "}
                <strong>{display(v, catalog)}</strong>
              </li>
            ))}
        </ul>
      )}
      {changes.map((c) => (
        <div key={c.code}>
          <strong>
            {c.code} · {c.title}
          </strong>
          <p>
            {date(c.before[0])} - {date(c.before[1])}
            <br />→ {date(c.after[0])} - {date(c.after[1])}
          </p>
        </div>
      ))}
      {Boolean(d.decision) && (
        <p>
          Решение:{" "}
          {{
            confirmed: "Подтверждён",
            dismissed: "Ложный сигнал",
            acknowledged: "Принят к сведению",
          }[String(d.decision)] || String(d.decision)}
        </p>
      )}
      {Boolean(d.note) && <p>Комментарий: {String(d.note)}</p>}
      {typeof d.revision === "number" && <p>Ревизия: {d.revision}</p>}
      {typeof d.sample_seconds === "number" && (
        <p>Частота анализа: один кадр каждые {d.sample_seconds} с</p>
      )}
      {typeof d.regions === "number" && <p>Полигонов: {d.regions}</p>}
    </div>
  );
}
