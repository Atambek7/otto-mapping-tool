"use client";

import React, { useEffect, useState } from "react";
import { createPortal } from "react-dom";

export interface HoverImagePreviewState {
  url: string;
  name?: string;
  oldPrice?: string;
  restartPrice?: string;
  ean?: string;
  category?: string;
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
  const [theme, setTheme] = useState<"dark" | "light">("dark");

  useEffect(() => {
    setMounted(true);
    const updateTheme = () => {
      const appEl = document.querySelector(".restart-app");
      if (appEl) {
        const currentTheme = appEl.getAttribute("data-theme") as "dark" | "light" | null;
        if (currentTheme === "light" || currentTheme === "dark") {
          setTheme(currentTheme);
        }
      }
    };
    updateTheme();
    const appEl = document.querySelector(".restart-app");
    if (appEl && typeof MutationObserver !== "undefined") {
      const observer = new MutationObserver(() => updateTheme());
      observer.observe(appEl, { attributes: true, attributeFilter: ["data-theme"] });
      return () => observer.disconnect();
    }
  }, []);

  if (!mounted || !preview || !preview.url) return null;

  // Prefer active DOM attribute at time of render to avoid any sync lag
  const domTheme =
    typeof document !== "undefined"
      ? (document.querySelector(".restart-app")?.getAttribute("data-theme") as "dark" | "light" | null)
      : null;
  const effectiveTheme = domTheme || theme || "dark";

  // Adaptive preview size
  const maxAvailableHeight =
    typeof window !== "undefined" ? window.innerHeight - 80 : 540;
  const imageSize = Math.max(300, Math.min(460, maxAvailableHeight - 110));

  const PREVIEW_WIDTH = imageSize + 24;
  const PREVIEW_HEIGHT = imageSize + 96;

  // Place preview to the right of thumbnail by default
  let left = preview.rect.right + 16;
  // If overflowing right edge of viewport, flip to left of thumbnail
  if (
    typeof window !== "undefined" &&
    left + PREVIEW_WIDTH > window.innerWidth - 16
  ) {
    left = Math.max(16, preview.rect.left - PREVIEW_WIDTH - 16);
  }

  // Vertically center relative to thumbnail, clamped within viewport
  const centerY = preview.rect.top + preview.rect.height / 2;
  const maxTop =
    typeof window !== "undefined"
      ? Math.max(16, window.innerHeight - PREVIEW_HEIGHT - 16)
      : 16;
  const top = Math.max(16, Math.min(maxTop, centerY - PREVIEW_HEIGHT / 2));

  return createPortal(
    <div
      className="catalog-hover-preview"
      data-theme={effectiveTheme}
      style={{
        top: `${top}px`,
        left: `${left}px`,
        width: `${PREVIEW_WIDTH}px`,
      }}
    >
      <div className="hover-preview-card">
        <div
          className="hover-preview-image-wrap"
          style={{ width: `${imageSize}px`, height: `${imageSize}px` }}
        >
          <img
            src={preview.url}
            alt={preview.name || "Превью товара"}
            className="hover-preview-img"
            loading="eager"
          />
        </div>

        <div className="hover-preview-info">
          {preview.name && (
            <p className="hover-preview-title" title={preview.name}>
              {preview.name}
            </p>
          )}

          <div className="hover-preview-meta-row">
            {preview.oldPrice && (
              <span className="price-single">{preview.oldPrice}</span>
            )}

            {preview.ean && (
              <span className="hover-preview-ean">EAN: {preview.ean}</span>
            )}
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
}
