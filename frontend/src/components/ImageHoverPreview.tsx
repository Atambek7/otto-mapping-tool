"use client";

import React, { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/utils";

export interface HoverImagePreviewState {
  url: string;
  name?: string;
  price?: string;
  isMirrored?: boolean;
  rect: {
    top: number;
    left: number;
    right: number;
    bottom: number;
    height: number;
  };
}

interface ImageHoverPreviewProps {
  preview: HoverImagePreviewState | null;
}

export function ImageHoverPreview({ preview }: ImageHoverPreviewProps) {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted || !preview || !preview.url) return null;

  // Large image size (up to 520px) adapted to viewport
  const maxAvailableHeight = typeof window !== "undefined" ? window.innerHeight - 80 : 620;
  const imageSize = Math.max(360, Math.min(520, maxAvailableHeight - 80));

  const PREVIEW_WIDTH = imageSize + 24;
  const PREVIEW_HEIGHT = imageSize + 76;

  // Place preview to the right of thumbnail by default
  let left = preview.rect.right + 16;
  // If overflowing right edge of viewport, flip to left of thumbnail
  if (typeof window !== "undefined" && left + PREVIEW_WIDTH > window.innerWidth - 16) {
    left = Math.max(16, preview.rect.left - PREVIEW_WIDTH - 16);
  }

  // Vertically center relative to thumbnail, clamped within viewport
  const centerY = preview.rect.top + preview.rect.height / 2;
  const maxTop = typeof window !== "undefined" ? Math.max(16, window.innerHeight - PREVIEW_HEIGHT - 16) : 16;
  const top = Math.max(16, Math.min(maxTop, centerY - PREVIEW_HEIGHT / 2));

  return createPortal(
    <div
      className="fixed z-[99999] pointer-events-none transition-all duration-150 animate-in fade-in-0 zoom-in-95"
      style={{
        top: `${top}px`,
        left: `${left}px`,
        width: `${PREVIEW_WIDTH}px`,
      }}
    >
      <div className="bg-white/95 dark:bg-slate-900/95 backdrop-blur-md border border-slate-200/80 dark:border-slate-800 rounded-2xl shadow-2xl p-3 flex flex-col gap-2.5 ring-1 ring-black/5 dark:ring-white/10">
        <div
          className="bg-slate-50 dark:bg-slate-950/60 rounded-xl overflow-hidden flex items-center justify-center border border-slate-100 dark:border-slate-800/80 shadow-inner relative"
          style={{ width: `${imageSize}px`, height: `${imageSize}px` }}
        >
          <img
            src={preview.url}
            alt=""
            className={cn("w-full h-full object-contain p-2 transition-transform", preview.isMirrored && "-scale-x-100")}
            loading="eager"
          />
          {preview.isMirrored && (
            <div className="absolute top-2 left-2 px-2 py-0.5 bg-purple-600/90 text-white text-[10px] font-semibold rounded-md shadow-xs backdrop-blur-xs">
              Отражено (XL)
            </div>
          )}
        </div>
        {preview.name && (
          <div className="px-1 pb-0.5">
            <p className="text-sm font-medium text-slate-800 dark:text-slate-100 line-clamp-2 leading-snug">
              {preview.name}
            </p>
            {preview.price && preview.price !== "-" && (
              <p className="text-sm font-semibold text-indigo-600 dark:text-indigo-400 mt-1">
                {preview.price} €
              </p>
            )}
          </div>
        )}
      </div>
    </div>,
    document.body
  );
}
