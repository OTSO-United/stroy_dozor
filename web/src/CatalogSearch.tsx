import { useEffect, useId, useMemo, useState } from "react";
import { ChevronDown, Search } from "lucide-react";
import type { Catalog } from "./api";

const normalize = (value: string) =>
  value.toLocaleLowerCase("ru").replaceAll("ё", "е");

export function CatalogSearch({
  works,
  code,
  onSelect,
}: {
  works: Catalog["works"];
  code: string;
  onSelect: (work: Catalog["works"][number]) => void;
}) {
  const id = useId();
  const [open, setOpen] = useState(false),
    [query, setQuery] = useState(""),
    [active, setActive] = useState(0);
  const selected = works.find((w) => w.code === code);
  const matches = useMemo(() => {
    const words = normalize(query).trim().split(/\s+/).filter(Boolean);
    return works.filter((w) =>
      words.every((word) => normalize(`${w.code} ${w.title}`).includes(word)),
    );
  }, [query, works]);
  const choose = (work: Catalog["works"][number]) => {
    onSelect(work);
    setOpen(false);
    setQuery("");
  };
  useEffect(() => {
    if (open)
      document
        .getElementById(`${id}-option-${active}`)
        ?.scrollIntoView({ block: "nearest" });
  }, [active, open, id]);
  const expand = () => {
    setOpen(true);
    setQuery("");
    setActive(0);
  };
  return (
    <div
      className="catalog-search"
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget)) setOpen(false);
      }}
    >
      <label htmlFor={id}>Работа из справочника</label>
      <div className="catalog-input">
        <Search size={16} />
        <input
          id={id}
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={open}
          aria-controls={`${id}-list`}
          aria-activedescendant={
            open && matches[active] ? `${id}-option-${active}` : undefined
          }
          autoComplete="off"
          placeholder="Поиск по словам или коду работы"
          value={
            open
              ? query
              : selected
                ? `${selected.code} · ${selected.title}`
                : ""
          }
          onFocus={expand}
          onClick={() => {
            if (!open) expand();
          }}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
            setActive(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              e.stopPropagation();
              setOpen(false);
            }
            if (e.key === "ArrowDown") {
              e.preventDefault();
              if (!open) expand();
              else setActive((i) => Math.min(i + 1, matches.length - 1));
            }
            if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((i) => Math.max(0, i - 1));
            }
            if (e.key === "Enter" && open) {
              e.preventDefault();
              if (matches[active]) choose(matches[active]);
            }
          }}
        />
        <button
          type="button"
          aria-label="Открыть справочник работ"
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => (open ? setOpen(false) : expand())}
        >
          <ChevronDown size={17} />
        </button>
      </div>
      {open && (
        <div
          className="catalog-results"
          id={`${id}-list`}
          role="listbox"
          aria-label="Работы из справочника"
        >
          {matches.length ? (
            matches.map((work, i) => (
              <button
                type="button"
                role="option"
                aria-selected={i === active}
                id={`${id}-option-${i}`}
                key={work.code}
                tabIndex={-1}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => choose(work)}
                onMouseEnter={() => setActive(i)}
              >
                <code>{work.code}</code>
                <span>{work.title}</span>
              </button>
            ))
          ) : (
            <p role="status">Работ по этому запросу не найдено</p>
          )}
        </div>
      )}
    </div>
  );
}
