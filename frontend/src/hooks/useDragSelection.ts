import { useState, useRef, useEffect, useCallback } from "react";

export function useDragSelection<T extends { id: string }>(items: T[]) {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  
  const isDragging = useRef(false);
  const dragStartIndex = useRef<number | null>(null);
  const dragStartX = useRef(0);
  const dragStartY = useRef(0);
  const lastDragIndex = useRef<number | null>(null);
  const suppressClick = useRef(false);
  const initialSelectedIds = useRef<Set<string>>(new Set());
  const dragSelectMode = useRef(false);
  // Keep items in ref so effect doesn't recreate on every render
  const itemsRef = useRef(items);
  itemsRef.current = items;
  // Keep selectedIds in ref for use inside effect without re-subscribing
  const selectedIdsRef = useRef(selectedIds);
  selectedIdsRef.current = selectedIds;

  // Throttle helper
  const lastMoveTime = useRef(0);
  // Auto-scroll state
  const scrollRafId = useRef<number | null>(null);
  const mouseClientY = useRef(0);

  const handleCardMouseDown = useCallback((index: number, id: string, e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest('.checkbox-area, [data-no-drag]')) return;
    dragStartIndex.current = index;
    dragStartX.current = e.clientX;
    dragStartY.current = e.clientY;
    lastDragIndex.current = index;
    isDragging.current = false;
    suppressClick.current = false;
    initialSelectedIds.current = new Set(selectedIdsRef.current);
    dragSelectMode.current = !selectedIdsRef.current.has(id);
  }, []);

  const applyDragRange = useCallback((index: number) => {
    if (dragStartIndex.current === null) return;
    const start = Math.min(dragStartIndex.current, index);
    const end = Math.max(dragStartIndex.current, index);
    const baseSelection = initialSelectedIds.current;
    setSelectedIds(() => {
      const next = new Set(baseSelection);
      for (let i = start; i <= end; i++) {
        const id = itemsRef.current[i]?.id;
        if (!id) continue;
        if (dragSelectMode.current) next.add(id); else next.delete(id);
      }
      return next;
    });
  }, []);

  // Auto-scroll when dragging near top/bottom of viewport
  const stopAutoScroll = useCallback(() => {
    if (scrollRafId.current !== null) {
      cancelAnimationFrame(scrollRafId.current);
      scrollRafId.current = null;
    }
  }, []);

  const startAutoScroll = useCallback(() => {
    if (scrollRafId.current !== null) return;
    const tick = () => {
      if (!isDragging.current) { stopAutoScroll(); return; }
      const ZONE = 80; // px from edge
      const SPEED = 12;
      const y = mouseClientY.current;
      const vh = window.innerHeight;
      if (y < ZONE) {
        window.scrollBy(0, -SPEED * (1 - y / ZONE));
      } else if (y > vh - ZONE) {
        window.scrollBy(0, SPEED * ((y - (vh - ZONE)) / ZONE));
      }
      // After scroll, re-evaluate which card we're over
      const el = document.elementFromPoint(mouseClientY.current, mouseClientY.current);
      scrollRafId.current = requestAnimationFrame(tick);
    };
    scrollRafId.current = requestAnimationFrame(tick);
  }, [stopAutoScroll]);

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (dragStartIndex.current === null) return;

      mouseClientY.current = e.clientY;

      const dx = e.clientX - dragStartX.current;
      const dy = e.clientY - dragStartY.current;
      const distance = Math.sqrt(dx * dx + dy * dy);

      if (!isDragging.current) {
        if (distance <= 5) return;
        isDragging.current = true;
        suppressClick.current = true;
        startAutoScroll();
      }

      // Throttle to 30fps to prevent lag on large lists
      const now = performance.now();
      if (now - lastMoveTime.current < 33) return;
      lastMoveTime.current = now;

      const element = document.elementFromPoint(e.clientX, e.clientY);
      if (!element) return;
      const card = (element as HTMLElement).closest('[data-factory-id]');
      if (!card) return;
      const factoryId = card.getAttribute('data-factory-id');
      if (!factoryId) return;
      const index = itemsRef.current.findIndex((item) => item.id === factoryId);
      if (index === -1 || index === lastDragIndex.current) return;
      lastDragIndex.current = index;
      applyDragRange(index);
    };
    
    const handleMouseUp = () => {
      if (dragStartIndex.current === null) return;
      if (isDragging.current) suppressClick.current = true;
      stopAutoScroll();
      setTimeout(() => { suppressClick.current = false; }, 50);
      isDragging.current = false;
      dragStartIndex.current = null;
      lastDragIndex.current = null;
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
      stopAutoScroll();
    };
  // No deps on items/selectedIds — we read them via refs
  }, [applyDragRange, startAutoScroll, stopAutoScroll]);

  return {
    selectedIds,
    setSelectedIds,
    handleCardMouseDown,
    applyDragRange,
    suppressClick,
  };
}
