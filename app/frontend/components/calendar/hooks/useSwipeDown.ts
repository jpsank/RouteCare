import { useCallback, useRef, useState } from "react";

const THRESHOLD = 80;

/**
 * Enables swipe-down-to-close on a mobile bottom sheet.
 * Attach `handleProps` to the drag handle element, and apply
 * `sheetStyle` to the sheet container.
 */
export function useSwipeDown(onClose: () => void) {
  const startY = useRef(0);
  const [offsetY, setOffsetY] = useState(0);
  const [dragging, setDragging] = useState(false);

  const onTouchStart = useCallback((e: React.TouchEvent) => {
    startY.current = e.touches[0].clientY;
    setDragging(true);
  }, []);

  const onTouchMove = useCallback((e: React.TouchEvent) => {
    if (!dragging) return;
    const delta = e.touches[0].clientY - startY.current;
    setOffsetY(Math.max(0, delta));
  }, [dragging]);

  const onTouchEnd = useCallback(() => {
    if (offsetY > THRESHOLD) {
      onClose();
    }
    setOffsetY(0);
    setDragging(false);
  }, [offsetY, onClose]);

  const handleProps = { onTouchStart, onTouchMove, onTouchEnd };

  const sheetStyle: React.CSSProperties = offsetY > 0
    ? { transform: `translateY(${offsetY}px)`, transition: dragging ? "none" : "transform 0.2s ease-out" }
    : {};

  return { handleProps, sheetStyle };
}
