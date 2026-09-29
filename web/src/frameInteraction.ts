export type FrameAnchor = { x: number; y: number };

export function bindFrameWheel(
  viewport: HTMLElement,
  zoom: number,
  changeZoom: (value: number, anchor?: FrameAnchor) => void,
): () => void {
  const onWheel = (event: WheelEvent) => {
    const unit =
      event.deltaMode === 1
        ? 16
        : event.deltaMode === 2
          ? viewport.clientHeight
          : 1;
    if (event.ctrlKey) {
      event.preventDefault();
      const bounds = viewport.getBoundingClientRect();
      changeZoom(
        Math.min(400, Math.max(100, zoom + (event.deltaY < 0 ? 25 : -25))),
        { x: event.clientX - bounds.left, y: event.clientY - bounds.top },
      );
      return;
    }
    if (event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
      event.preventDefault();
      viewport.scrollLeft += (event.deltaX || event.deltaY) * unit;
      return;
    }
    if (event.deltaY !== 0) {
      event.preventDefault();
      viewport.scrollTop += event.deltaY * unit;
    }
  };
  viewport.addEventListener("wheel", onWheel, { passive: false });
  return () => viewport.removeEventListener("wheel", onWheel);
}
