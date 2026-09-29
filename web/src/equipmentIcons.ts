/** Icons for the 20 classes supported by the current YOLO equipment model. */
export const equipmentIconIds = [
  0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 16, 17, 18, 19, 20, 28,
] as const;

const equipmentIconIdSet: ReadonlySet<number> = new Set(equipmentIconIds);

export function equipmentIconUrl(classId: string | number): string | null {
  const id = Number(classId);
  return Number.isInteger(id) && equipmentIconIdSet.has(id)
    ? `/equipment-icons/${id}.webp`
    : null;
}
