import { useEffect, useId, useRef, useState } from "react";

export function InfoButton({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeEscape);
    };
  }, [open]);
  return (
    <div className="panel-info" ref={root}>
      <button
        type="button"
        className="panel-info-button"
        aria-label={`Информация: ${title}`}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((value) => !value)}
      >
        ?
      </button>
      {open && (
        <div id={id} className="panel-info-popover" role="note">
          <strong>{title}</strong>
          <div>{children}</div>
        </div>
      )}
    </div>
  );
}
