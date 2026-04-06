import { useCallback, useEffect, useRef, useState } from "react";

const THRESHOLD = 80;

/**
 * Enables swipe-down-to-close on a mobile bottom sheet.
 * Attach `handleRef` to the drag handle element, spread `handleProps`
 * onto it for start/end, and apply `sheetStyle` to the sheet container.
 *
 * Uses a native touchmove listener with { passive: false } so we can
 * preventDefault and block Safari's pull-to-refresh.
 */
export function useSwipeDown(onClose: () => void) {
  const startY = useRef(0);
  const [offsetY, setOffsetY] = useState(0);
  const dragging = useRef(false);
  const handleRef = useRef<HTMLDivElement | null>(null);

  const onTouchStart = useCallback((e: TouchEvent) => {
    startY.current = e.touches[0].clientY;
    dragging.current = true;
  }, []);

  const onTouchMove = useCallback((e: TouchEvent) => {
    if (!dragging.current) return;
    const delta = e.touches[0].clientY - startY.current;
    if (delta > 0) {
      e.preventDefault(); // block Safari pull-to-refresh
      setOffsetY(delta);
    }
  }, []);

  const onTouchEnd = useCallback(() => {
    if (!dragging.current) return;
    dragging.current = false;
    setOffsetY((prev) => {
      if (prev > THRESHOLD) onClose();
      return 0;
    });
  }, [onClose]);

  useEffect(() => {
    const el = handleRef.current;
    if (!el) return;
    el.addEventListener("touchstart", onTouchStart, { passive: true });
    el.addEventListener("touchmove", onTouchMove, { passive: false });
    el.addEventListener("touchend", onTouchEnd, { passive: true });
    return () => {
      el.removeEventListener("touchstart", onTouchStart);
      el.removeEventListener("touchmove", onTouchMove);
      el.removeEventListener("touchend", onTouchEnd);
    };
  }, [onTouchStart, onTouchMove, onTouchEnd]);

  const sheetStyle: React.CSSProperties = offsetY > 0
    ? { transform: `translateY(${offsetY}px)`, transition: "none" }
    : {};

  return { handleRef, sheetStyle };
}
