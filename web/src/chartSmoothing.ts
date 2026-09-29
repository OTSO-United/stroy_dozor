export const SMOOTHING_STEPS = [
  [30, "30 секунд"],
  [60, "1 минута"],
  [180, "3 минуты"],
  [300, "5 минут"],
  [900, "15 минут"],
  [1800, "30 минут"],
  [3600, "1 час"],
  [7200, "2 часа"],
  [14400, "4 часа"],
  [28800, "8 часов"],
  [43200, "12 часов"],
  [86400, "1 сутки"],
] as const;

export function defaultSmoothingStep(sampleSeconds?: number): number {
  const interval =
    sampleSeconds !== undefined &&
    Number.isFinite(sampleSeconds) &&
    sampleSeconds > 0
      ? sampleSeconds
      : 1;
  const minimum = interval * 6;
  return (
    SMOOTHING_STEPS.find(([seconds]) => seconds >= minimum)?.[0] ??
    SMOOTHING_STEPS.at(-1)![0]
  );
}
