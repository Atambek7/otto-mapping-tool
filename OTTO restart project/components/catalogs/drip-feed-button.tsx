"use client";

import React from "react";
import { Clock } from "lucide-react";

interface DripFeedButtonProps {
  disabled?: boolean;
  className?: string;
  onClick?: () => void;
}

/**
 * DripFeedButton:
 * Компонент кнопки запуска/настройки Drip Feed.
 * Согласно требованиям: "он пока ничего не открывает при нажатии".
 */
export function DripFeedButton({
  disabled = false,
  className = "",
  onClick,
}: DripFeedButtonProps) {
  function handleClick(e: React.MouseEvent<HTMLButtonElement>) {
    e.preventDefault();
    // На данный момент кнопка ничего не открывает при нажатии
    if (onClick) {
      onClick();
    }
  }

  return (
    <button
      type="button"
      className={`catalog-action-btn drip-feed-btn ${className}`}
      disabled={disabled}
      onClick={handleClick}
      title="Drip Feed: планирование постепенной выгрузки (в разработке)"
      aria-label="Drip Feed"
    >
      <Clock size={14} className="drip-feed-clock-icon" />
      <span>Drip Feed</span>
    </button>
  );
}
