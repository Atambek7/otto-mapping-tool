"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowRight, Check, Lightbulb, X, RotateCcw } from "lucide-react";

export function WelcomeTipCard() {
  const [dismissed, setDismissed] = useState(false);

  if (dismissed) {
    return (
      <div style={{ display: "flex", justifyContent: "flex-end", paddingTop: "8px" }}>
        <button
          type="button"
          className="tip-collapsed-pill"
          onClick={() => setDismissed(false)}
          title="Показать блок советов"
        >
          <Lightbulb size={14} />
          <span>Показать совет</span>
        </button>
      </div>
    );
  }

  return (
    <aside className="welcome-tip-card" role="complementary" aria-labelledby="tip-title">
      <div className="tip-card-header">
        <div className="tip-icon-title">
          <div className="tip-badge-icon">
            <Lightbulb size={17} strokeWidth={2.4} />
          </div>
          <h4 id="tip-title" className="tip-header-title">
            Совет
          </h4>
        </div>

        <button
          type="button"
          className="tip-close-btn"
          onClick={() => setDismissed(true)}
          title="Скрыть совет"
          aria-label="Скрыть совет"
        >
          <X size={15} />
        </button>
      </div>

      <p className="tip-main-text">
        Начните с раздела «Кандидаты 3/12M», если хотите быстро приступить к
        обработке товаров Restart.
      </p>

      <div className="tip-checklist">
        <div className="tip-check-item">
          <div className="tip-check-icon">
            <Check size={11} strokeWidth={3} />
          </div>
          <span>Ознакомьтесь с основными разделами.</span>
        </div>

        <div className="tip-check-item">
          <div className="tip-check-icon">
            <Check size={11} strokeWidth={3} />
          </div>
          <span>Используйте OTTO Library для работы с данными.</span>
        </div>

        <div className="tip-check-item">
          <div className="tip-check-icon">
            <Check size={11} strokeWidth={3} />
          </div>
          <span>Проверяйте товары перед публикацией.</span>
        </div>
      </div>

      <Link href="/afterbuy" className="tip-cta-btn">
        <span>Перейти к каталогам Afterbuy</span>
        <ArrowRight size={14} />
      </Link>
    </aside>
  );
}
