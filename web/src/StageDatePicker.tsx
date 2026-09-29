import { useId, useState } from "react";
import { CalendarDays, ChevronLeft, ChevronRight } from "lucide-react";

const two = (n: number) => String(n).padStart(2, "0");
const dayKey = (d: Date) =>
  `${d.getUTCFullYear()}-${two(d.getUTCMonth() + 1)}-${two(d.getUTCDate())}`;

export function StageDatePicker({
  value,
  onChange,
  label = "Новая дата и время окончания, МСК",
  part = "окончания",
}: {
  value: string;
  onChange: (value: string) => void;
  label?: string;
  part?: string;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [month, setMonth] = useState(() => value.slice(0, 7));
  const [day, time = "00:00"] = value.split("T");
  const [hour, minute] = time.split(":");
  const [year, monthNumber] = month.split("-").map(Number);
  const start = new Date(Date.UTC(year, monthNumber - 1, 1));
  const firstWeekday = (start.getUTCDay() + 6) % 7;
  const days = new Date(Date.UTC(year, monthNumber, 0)).getUTCDate();
  const changeMonth = (delta: number) =>
    setMonth(
      dayKey(new Date(Date.UTC(year, monthNumber - 1 + delta, 1))).slice(0, 7),
    );
  return (
    <div className="stage-date-picker">
      <p id={id} className="field-label">
        {label}
      </p>
      <div className="stage-date-controls" aria-labelledby={id}>
        <button
          type="button"
          className="button secondary"
          aria-label={`Выбрать дату ${part}`}
          aria-expanded={open}
          aria-controls={`${id}-calendar`}
          onClick={() => {
            if (!open) setMonth(day.slice(0, 7));
            setOpen((v) => !v);
          }}
        >
          <CalendarDays size={18} />
          {new Date(`${day}T00:00:00Z`).toLocaleDateString("ru", {
            timeZone: "UTC",
          })}
        </button>
        <div
          className="clock-time-group"
          role="group"
          aria-label={`Время ${part} этапа`}
        >
          <select
            aria-label={`Часы ${part} этапа`}
            value={hour}
            onChange={(e) => onChange(`${day}T${e.target.value}:${minute}`)}
          >
            {Array.from({ length: 24 }, (_, h) => (
              <option key={h} value={two(h)}>
                {two(h)}
              </option>
            ))}
          </select>
          <span aria-hidden="true">:</span>
          <select
            aria-label={`Минуты ${part} этапа`}
            value={minute}
            onChange={(e) => onChange(`${day}T${hour}:${e.target.value}`)}
          >
            {Array.from({ length: 60 }, (_, m) => (
              <option key={m} value={two(m)}>
                {two(m)}
              </option>
            ))}
          </select>
        </div>
      </div>
      {open && (
        <div
          className="stage-calendar"
          id={`${id}-calendar`}
          role="group"
          aria-label={`Календарь ${part} этапа`}
        >
          <div className="calendar-month">
            <button
              type="button"
              className="icon-button"
              aria-label="Предыдущий месяц"
              onClick={() => changeMonth(-1)}
            >
              <ChevronLeft size={20} />
            </button>
            <strong aria-live="polite">
              {start.toLocaleDateString("ru", {
                month: "long",
                year: "numeric",
                timeZone: "UTC",
              })}
            </strong>
            <button
              type="button"
              className="icon-button"
              aria-label="Следующий месяц"
              onClick={() => changeMonth(1)}
            >
              <ChevronRight size={20} />
            </button>
          </div>
          <div className="calendar-days">
            {["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map((w) => (
              <span key={w}>{w}</span>
            ))}
            {Array.from({ length: firstWeekday }, (_, i) => (
              <span key={`empty-${i}`} aria-hidden="true" />
            ))}
            {Array.from({ length: days }, (_, i) => {
              const date = new Date(Date.UTC(year, monthNumber - 1, i + 1));
              const key = dayKey(date);
              return (
                <button
                  key={key}
                  type="button"
                  aria-pressed={day === key}
                  className={day === key ? "selected" : ""}
                  aria-label={date.toLocaleDateString("ru", {
                    day: "numeric",
                    month: "long",
                    year: "numeric",
                    timeZone: "UTC",
                  })}
                  onClick={() => {
                    onChange(`${key}T${time}`);
                    setOpen(false);
                  }}
                >
                  {i + 1}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
