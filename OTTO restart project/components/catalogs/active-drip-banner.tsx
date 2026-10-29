"use client";

import React, { useState } from "react";
import { Clock, Layers, Pause, Play } from "lucide-react";
import type { DripPlan } from "@/features/catalogs/drip-feed-service";

interface ActiveDripBannerProps {
  plan: DripPlan;
  onOpenQueue: () => void;
  onTogglePause?: (planId: number) => void;
}

export function ActiveDripBanner({
  plan,
  onOpenQueue,
  onTogglePause,
}: ActiveDripBannerProps) {
  const [localStatus, setLocalStatus] = useState<"active" | "paused">(
    plan.status === "paused" ? "paused" : "active"
  );

  const isPaused = localStatus === "paused";

  const handlePauseToggle = () => {
    const nextStatus = isPaused ? "active" : "paused";
    setLocalStatus(nextStatus);
    onTogglePause?.(plan.id);
  };

  return (
    <div className="active-drip-banner">
      <div className="drip-banner-left">
        <div
          className={`drip-banner-icon-box ${
            isPaused ? "icon-box-paused" : "icon-box-running"
          }`}
        >
          <Clock size={16} />
        </div>
        <div className="drip-banner-content">
          <div className="drip-banner-heading">
            <span className="drip-banner-title">
              Активный Drip Feed: День {plan.current_day} из {plan.total_days}
            </span>
            <span
              className={`drip-banner-badge ${
                isPaused ? "badge-paused" : "badge-running"
              }`}
            >
              {isPaused ? "На паузе" : "В процессе"}
            </span>
          </div>

          <p className="drip-banner-subtitle">
            Ежедневно в {plan.daily_time}{" "}
            <strong className="text-accent">
              {plan.progress?.published || 0}
            </strong>{" "}
            / {plan.total_products} товаров
            {plan.progress?.existing && plan.progress.existing > 0 ? (
              <span className="drip-banner-existing-counter">
                {" "}· {plan.progress.existing} есть в OTTO
              </span>
            ) : null}
            {plan.progress?.failed && plan.progress.failed > 0 ? (
              <span className="drip-banner-error-counter">
                {" "}· ошибок: {plan.progress.failed}
              </span>
            ) : null}
          </p>

          <div className="drip-banner-progress-wrap">
            <div
              className="drip-banner-progress-fill"
              style={{ width: `${plan.progress?.percentage || 0}%` }}
            />
          </div>
        </div>
      </div>

      <div className="drip-banner-actions">
        <button
          type="button"
          onClick={onOpenQueue}
          className="drip-banner-btn-secondary"
          title="Посмотреть список товаров этого плана"
        >
          <Layers size={14} />
          <span>Список товаров</span>
        </button>

        <button
          type="button"
          onClick={handlePauseToggle}
          className={`drip-banner-btn-toggle ${
            isPaused ? "btn-resume" : "btn-pause"
          }`}
          title={isPaused ? "Возобновить выполнение плана" : "Приостановить выполнение плана"}
        >
          {isPaused ? (
            <>
              <Play size={13} style={{ fill: "currentColor" }} />
              <span>Возобновить</span>
            </>
          ) : (
            <>
              <Pause size={13} />
              <span>Пауза</span>
            </>
          )}
        </button>
      </div>
    </div>
  );
}
