"use client";

import Link from "next/link";
import {
  ArrowRight,
  ChevronDown,
  Barcode,
  RefreshCw,
  PackageCheck,
} from "lucide-react";

export function WelcomeHero() {
  function handleScrollToSteps(e: React.MouseEvent) {
    e.preventDefault();
    const target = document.getElementById("quick-route");
    if (target) {
      target.scrollIntoView({ behavior: "smooth" });
    }
  }

  return (
    <article className="welcome-hero-card">
      <div className="welcome-hero-content">
        <h2 className="welcome-hero-title">Дайте товарам второй шанс</h2>

        <p className="welcome-hero-desc">
          Инструмент реактивации ассортимента: анализируем снятые с продажи
          позиции, оптимизируем цены, обновляем характеристики и регистрируем
          новый EAN для возвращения товаров в активный каталог.
        </p>

        <div className="welcome-hero-actions">
          <Link href="/afterbuy" className="btn-hero-primary">
            <span>Перейти к товарам 3/12M</span>
            <ArrowRight size={15} aria-hidden="true" />
          </Link>

          <a
            href="#quick-route"
            onClick={handleScrollToSteps}
            className="btn-hero-secondary"
          >
            <span>Узнать больше</span>
            <ChevronDown size={14} aria-hidden="true" />
          </a>
        </div>
      </div>

      {/* Static Conceptual Transformation Story: Старый EAN → Restart → Новый EAN */}
      <div
        className="welcome-transform-story"
        aria-label="Схема трансформации: старый EAN в новый EAN через Restart"
      >
        <div className="transform-track">
          {/* Step 1: Старый EAN */}
          <div className="transform-node transform-node-old">
            <div className="transform-node-top">
              <span className="transform-node-icon">
                <Barcode size={16} aria-hidden="true" />
              </span>
              <span className="transform-node-badge">3/12M</span>
            </div>
            <div className="transform-node-name">Старый EAN</div>
            <p className="transform-node-meta">Снят с публикации</p>
          </div>

          {/* Connector 1 */}
          <div className="transform-connector" aria-hidden="true">
            <span className="transform-connector-line" />
            <ArrowRight size={14} className="transform-connector-arrow" />
          </div>

          {/* Step 2: Restart */}
          <div className="transform-node transform-node-core">
            <div className="transform-node-top">
              <span className="transform-node-icon accent">
                <RefreshCw size={16} aria-hidden="true" />
              </span>
              <span className="transform-node-badge accent">Restart</span>
            </div>
            <div className="transform-node-name">Оптимизация</div>
            <p className="transform-node-meta">Скидка до -15€ · ИИ и QC</p>
          </div>

          {/* Connector 2 */}
          <div className="transform-connector" aria-hidden="true">
            <span className="transform-connector-line" />
            <ArrowRight size={14} className="transform-connector-arrow" />
          </div>

          {/* Step 3: Новый EAN */}
          <div className="transform-node transform-node-new">
            <div className="transform-node-top">
              <span className="transform-node-icon success">
                <PackageCheck size={16} aria-hidden="true" />
              </span>
              <span className="transform-node-badge success">OTTO</span>
            </div>
            <div className="transform-node-name">Новый EAN</div>
            <p className="transform-node-meta">Готов к продаже</p>
          </div>
        </div>
      </div>
    </article>
  );
}
