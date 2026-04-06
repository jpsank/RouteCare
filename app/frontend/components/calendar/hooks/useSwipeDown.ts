import { useCallback, useRef, useState } from "react";

const THRESHOLD = 80;

/**
 * Enables swipe-down-to-close on a mobile bottom sheet.
 * Attach `handleRef` (callback ref) to the drag handle element and
 * apply `sheetStyle` to the sheet container.
 *
 * Uses native touchmove with { passive: false } to block Safari
 * pull-to-refresh during the drag.
 */
export function useSwipeDown(onClose: () => void) {
  const startY = useRef(0);
  const [offsetY, setOffsetY] = useState(0);
  const dragging = useRef(false);
  const elRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  const onTouchStart = useCallback((e: TouchEvent) => {
    startY.current = e.touches[0].clientY;
    dragging.current = true;
  }, []);

  const onTouchMove = useCallback((e: TouchEvent) => {
    if (!dragging.current) return;
    const delta = e.touches[0].clientY - startY.current;
    if (delta > 0) {
      e.preventDefault();
      setOffsetY(delta);
    }
  }, []);

  const onTouchEnd = useCallback(() => {
    if (!dragging.current) return;
    dragging.current = false;
    setOffsetY((prev) => {
      if (prev > THRESHOLD) closeRef.current();
      return 0;
    });
  }, []);

  const handleRef = useCallback((node: HTMLDivElement | null) => {
    const prev = elRef.current;
    if (prev) {
      prev.removeEventListener("touchstart", onTouchStart);
      prev.removeEventListener("touchmove", onTouchMove);
      prev.removeEventListener("touchend", onTouchEnd);
    }
    elRef.current = node;
    if (node) {
      node.addEventListener("touchstart", onTouchStart, { passive: true });
      node.addEventListener("touchmove", onTouchMove, { passive: false });
      node.addEventListener("touchend", onTouchEnd, { passive: true });
    }
  }, [onTouchStart, onTouchMove, onTouchEnd]);

  const sheetStyle: React.CSSProperties = offsetY > 0
    ? { transform: `translateY(${offsetY}px)`, transition: "none" }
    : {};

  return { handleRef, sheetStyle };
}
