import type { Catalog } from "./api";

export function WorkResourcePreset({
  work,
  catalog,
  onApply,
}: {
  work: Catalog["works"][number] | undefined;
  catalog: Catalog;
  onApply: (counts: Record<string, number>) => void;
}) {
  if (!work) return null;
  const preset = work.resource_preset;
  return (
    <div className="work-resource-preset">
      <button
        type="button"
        className="text-button"
        onClick={() => onApply(preset.counts)}
      >
        Заменить количества стартовым составом
      </button>
    </div>
  );
}
