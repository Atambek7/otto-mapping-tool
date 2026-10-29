"use client";

import { useRestartAccount } from "@/features/state/restart-account";
import { WelcomeHero } from "./welcome-hero";
import { FeatureGrid } from "./feature-grid";
import { QuickStartSteps } from "./quick-start-steps";
import { ScreenPreviewGrid } from "./screen-preview-grid";
import { WelcomeTipCard } from "./welcome-tip-card";
import "./welcome.css";

export function WelcomePage() {
  const { account } = useRestartAccount();

  return (
    <div className="welcome-page">
      {/* SECTION A — Welcome Header */}
      <header className="welcome-header">
        <div className="welcome-header-left">
          <div className="welcome-eyebrow">
            <span>OTTO Restart 3/12M</span>
          </div>

          <h1 className="welcome-title">Добро пожаловать!</h1>

          <p className="welcome-subtitle">
            Инструмент подготовки и повторного запуска в продажу товаров OTTO, снятых после 3 или 12 месяцев.
          </p>
        </div>

        <div className="welcome-header-actions">
          <span className="welcome-account-pill">
            Аккаунт: <strong>{account}</strong>
          </span>
        </div>
      </header>

      {/* SECTION B & C — Main Row: Hero (58%) + Feature Grid (42%) */}
      <div className="welcome-top-grid">
        <WelcomeHero />
        <FeatureGrid />
      </div>

      {/* SECTION D — Quick Route 3 Steps */}
      <QuickStartSteps />

      {/* SECTION E & F — Main Screens (68%) + Tip Panel (32%) */}
      <div className="welcome-bottom-grid">
        <ScreenPreviewGrid />
        <WelcomeTipCard />
      </div>
    </div>
  );
}
