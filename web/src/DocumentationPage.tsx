import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Activity,
  ArrowRight,
  BookOpen,
  Camera,
  CheckCircle2,
  ClipboardList,
  FolderPlus,
  Maximize2,
  Search,
  X,
  ShieldCheck,
} from "lucide-react";
import { equipmentDocs, type EquipmentDoc } from "./documentationEquipment";
import "./documentation.css";

const workflow = [
  {
    number: "01",
    title: "Добавьте объект",
    text: "Укажите площадку, адрес и её тип. Объект станет общей точкой для плана, источников и результатов.",
    icon: FolderPlus,
  },
  {
    number: "02",
    title: "Задайте план",
    text: "Создайте этапы вручную или импортируйте CSV. Укажите сроки и требуемое количество техники.",
    icon: ClipboardList,
  },
  {
    number: "03",
    title: "Привяжите источник",
    text: "Добавьте видео или RTSP-камеру к одному или нескольким этапам. Выберите весь кадр либо зону контроля.",
    icon: Camera,
  },
  {
    number: "04",
    title: "Наблюдайте динамику",
    text: "Загруженное видео анализируется автоматически. Обзор показывает присутствие, активность и состав техники по этапу.",
    icon: Activity,
  },
  {
    number: "05",
    title: "Проверьте сигнал",
    text: "Откройте отклонение, сравните факт с планом и изучите подтверждающие кадры. Решение остаётся за оператором.",
    icon: ShieldCheck,
  },
  {
    number: "06",
    title: "Получите отчёт",
    text: "Сохраните короткий отчёт по значимым событиям и при необходимости повторите анализ источника.",
    icon: CheckCircle2,
  },
];

const equipmentGroups: {
  id: EquipmentDoc["group"];
  title: string;
  lead: string;
}[] = [
  {
    id: "earth",
    title: "Земляные и свайные работы",
    lead: "Разработка грунта, перемещение и подготовка основания.",
  },
  {
    id: "transport",
    title: "Доставка и перевозка",
    lead: "Материалы, бетон, жидкости и перевозка машин.",
  },
  {
    id: "lifting",
    title: "Подъём и подача",
    lead: "Подъём грузов, погрузка и подача бетона.",
  },
  {
    id: "road",
    title: "Дороги и содержание",
    lead: "Уплотнение, укладка покрытия и уборка территории.",
  },
];

function EquipmentCard({
  equipment,
  onOpenPhoto,
}: {
  equipment: EquipmentDoc;
  onOpenPhoto: (photo: { src: string; alt: string }) => void;
}) {
  return (
    <article className="docs-equipment-card">
      <div className="docs-equipment-photos">
        {[1, 2].map((index) => (
          <figure key={index}>
            <button
              type="button"
              className="docs-equipment-photo-button"
              onClick={() =>
                onOpenPhoto({
                  src:
                    "/docs/equipment/" + equipment.id + "-" + index + ".webp",
                  alt: equipment.name + ", пример " + index,
                })
              }
              aria-label={
                equipment.name + ": открыть пример " + index + " на весь экран"
              }
            >
              <img
                src={"/docs/equipment/" + equipment.id + "-" + index + ".webp"}
                alt=""
                loading="lazy"
                decoding="async"
                width="480"
                height="320"
              />
              <span className="docs-photo-expand" aria-hidden="true">
                <Maximize2 size={18} />
              </span>
            </button>
            <figcaption>Пример {index}</figcaption>
          </figure>
        ))}
      </div>
      <div className="docs-equipment-info">
        <div>
          <h3>{equipment.name}</h3>
          {equipment.detail && <small>({equipment.detail})</small>}
          <span>{equipment.english}</span>
        </div>
        <p>{equipment.description}</p>
      </div>
    </article>
  );
}

export function DocumentationPage({
  onOpenProjects,
}: {
  onOpenProjects: () => void;
}) {
  const [query, setQuery] = useState("");
  const [openPhoto, setOpenPhoto] = useState<{
    src: string;
    alt: string;
  } | null>(null);
  const closePhotoRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!openPhoto) return;
    const previousOverflow = document.body.style.overflow;
    const returnFocus = document.activeElement as HTMLElement | null;
    document.body.style.overflow = "hidden";
    closePhotoRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpenPhoto(null);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", onKeyDown);
      returnFocus?.focus();
    };
  }, [openPhoto]);
  const normalizedQuery = query.trim().toLocaleLowerCase("ru");
  const visible = equipmentDocs.filter((equipment) =>
    (
      equipment.name +
      " " +
      (equipment.detail || "") +
      " " +
      equipment.english +
      " " +
      equipment.description
    )
      .toLocaleLowerCase("ru")
      .includes(normalizedQuery),
  );

  return (
    <div className="docs-page">
      <section className="docs-hero">
        <div className="docs-hero-copy">
          <span className="eyebrow">РУКОВОДСТВО ПО ПРОДУКТУ</span>
          <h1>От плана к проверяемому факту</h1>
          <p>
            СтройДозор связывает этапы работ с кадрами камер и видео, показывает
            присутствие техники во времени и помогает быстро проверить
            отклонения. Каждый вывод можно открыть вместе с его основанием.
          </p>
          <div className="docs-hero-actions">
            <a className="button primary" href="#docs-workflow">
              Как начать <ArrowRight size={17} />
            </a>
            <a className="button secondary" href="#docs-equipment">
              Виды техники
            </a>
          </div>
        </div>
        <aside className="docs-hero-note">
          <div className="docs-hero-note-icon">
            <ShieldCheck size={24} />
          </div>
          <strong>Контроль с контекстом</strong>
          <p>
            Модель находит технику на кадре. Сопоставление с планом выполняется
            отдельно для выбранного этапа и зоны.
          </p>
          <span>Наличие машины не означает завершение работ.</span>
        </aside>
      </section>

      <section className="docs-section" id="docs-workflow">
        <div className="docs-section-heading">
          <span className="eyebrow">ПОЛЬЗОВАТЕЛЬСКИЙ ПУТЬ</span>
          <h2>Шесть шагов от площадки до отчёта</h2>
          <p>
            План задаёт ожидания. Камера показывает наблюдаемое. Оператор
            проверяет решение.
          </p>
        </div>
        <ol className="docs-workflow">
          {workflow.map((step) => (
            <li key={step.number}>
              <div className="docs-step-top">
                <span>{step.number}</span>
                <step.icon size={22} aria-hidden="true" />
              </div>
              <h3>{step.title}</h3>
              <p>{step.text}</p>
            </li>
          ))}
        </ol>
      </section>

      <section
        className="docs-section docs-equipment-section"
        id="docs-equipment"
      >
        <div className="docs-equipment-heading">
          <div className="docs-section-heading">
            <span className="eyebrow">ВОЗМОЖНОСТИ МОДЕЛИ</span>
            <h2>Техника, которую распознаёт приложение</h2>
            <p>
              {equipmentDocs.length} поддерживаемых видов. Для каждого показаны
              два отобранных реальных фотопримера техники.
            </p>
          </div>
          <label className="docs-search">
            <Search size={18} aria-hidden="true" />
            <span className="sr-only">Найти вид техники</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Найти технику"
            />
          </label>
        </div>
        {visible.length ? (
          equipmentGroups.map((group) => {
            const items = visible.filter(
              (equipment) => equipment.group === group.id,
            );
            return items.length ? (
              <div className="docs-equipment-group" key={group.id}>
                <div className="docs-group-heading">
                  <h3>{group.title}</h3>
                  <p>{group.lead}</p>
                  <span>{items.length}</span>
                </div>
                <div className="docs-equipment-grid">
                  {items.map((equipment) => (
                    <EquipmentCard
                      key={equipment.id}
                      equipment={equipment}
                      onOpenPhoto={setOpenPhoto}
                    />
                  ))}
                </div>
              </div>
            ) : null;
          })
        ) : (
          <p className="docs-search-empty">По запросу техника не найдена.</p>
        )}
        <p className="docs-catalog-note">
          Примеры показывают типы техники, а не гарантируют распознавание на
          любом кадре. При плохом обзоре или недостаточных данных результат
          может быть неизвестен. Настройки модели и качество камеры влияют на
          наблюдение. На контрольных фото модель путала погрузчики и не находила
          автопоезд с тралом.{" "}
          <a
            href="/docs/equipment/sources.html"
            target="_blank"
            rel="noreferrer"
          >
            Авторы и лицензии фотографий
          </a>
          .
        </p>
      </section>

      {openPhoto &&
        createPortal(
          <div
            className="docs-photo-viewer"
            role="presentation"
            onMouseDown={(event) => {
              if (event.target === event.currentTarget) setOpenPhoto(null);
            }}
          >
            <div
              className="docs-photo-viewer-content"
              role="dialog"
              aria-modal="true"
              aria-label={openPhoto.alt}
            >
              <button
                ref={closePhotoRef}
                type="button"
                className="docs-photo-viewer-close"
                onClick={() => setOpenPhoto(null)}
                aria-label="Закрыть изображение"
              >
                <X size={22} />
              </button>
              <img src={openPhoto.src} alt={openPhoto.alt} />
              <p>{openPhoto.alt}</p>
            </div>
          </div>,
          document.body,
        )}

      <section className="docs-finish">
        <div>
          <span className="eyebrow">НАЧНИТЕ С ОБЪЕКТА</span>
          <h2>Соберите план, источники и наблюдения в одном месте</h2>
        </div>
        <button
          type="button"
          className="button primary"
          onClick={onOpenProjects}
        >
          Открыть объекты <ArrowRight size={17} />
        </button>
      </section>
    </div>
  );
}
