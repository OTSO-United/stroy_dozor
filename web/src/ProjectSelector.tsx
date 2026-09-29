import { useEffect, useId, useRef, useState } from "react";
import { Building2, Check, ChevronDown } from "lucide-react";
import type { Project } from "./api";

export function ProjectSelector({
  projects,
  value,
  onChange,
}: {
  projects: Project[];
  value: string;
  onChange: (id: string) => void;
}) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const selected = projects.find((project) => project.id === value);
  useEffect(() => {
    if (open)
      document
        .getElementById(`${id}-${active}`)
        ?.scrollIntoView({ block: "nearest" });
  }, [active, open, id]);
  const expand = () => {
    setActive(
      Math.max(
        0,
        projects.findIndex((project) => project.id === value),
      ),
    );
    setOpen(true);
  };
  const choose = (project: Project) => {
    onChange(project.id);
    setOpen(false);
    trigger.current?.focus();
  };
  return (
    <div
      className="header-project-select"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
    >
      <button
        ref={trigger}
        type="button"
        className="project-selector-trigger"
        role="combobox"
        aria-label="Текущий объект"
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-controls={`${id}-list`}
        aria-activedescendant={
          open && projects[active] ? `${id}-${active}` : undefined
        }
        onClick={() => (open ? setOpen(false) : expand())}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            setOpen(false);
          }
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            if (!open) expand();
            else
              setActive((current) =>
                Math.max(
                  0,
                  Math.min(
                    projects.length - 1,
                    current + (event.key === "ArrowDown" ? 1 : -1),
                  ),
                ),
              );
          }
          if ((event.key === "Enter" || event.key === " ") && open) {
            event.preventDefault();
            if (projects[active]) choose(projects[active]);
          }
        }}
      >
        <Building2 size={18} />
        <span>{selected?.name || "Выберите объект"}</span>
        <ChevronDown size={17} />
      </button>
      {open && (
        <div
          id={`${id}-list`}
          role="listbox"
          aria-label="Объекты рабочего пространства"
          className="project-selector-options"
        >
          {projects.length ? (
            projects.map((project, index) => (
              <button
                key={project.id}
                id={`${id}-${index}`}
                type="button"
                role="option"
                tabIndex={-1}
                aria-selected={project.id === value}
                className={active === index ? "active" : ""}
                onMouseEnter={() => setActive(index)}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => choose(project)}
              >
                <span>{project.name}</span>
                {project.id === value && <Check size={15} />}
              </button>
            ))
          ) : (
            <p>Пока нет объектов</p>
          )}
        </div>
      )}
    </div>
  );
}
