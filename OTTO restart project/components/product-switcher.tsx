import Link from "next/link";
import { ChevronDown, Layers3 } from "lucide-react";

export function ProductSwitcher() {
  return (
    <details className="product-switcher">
      <summary aria-label="Выбрать продукт">
        <span className="product-mark"><Layers3 size={17} /></span>
        <span className="product-name">
          <strong>OttoRestart</strong>
          <small>Restart 3/12M</small>
        </span>
        <ChevronDown className="product-chevron" size={15} />
      </summary>
      <div className="product-menu">
        <Link href="/" className="product-choice selected">
          <span className="product-choice-dot restart-dot" />
          <span><strong>OttoRestart</strong><small>Управление Restart 3/12M</small></span>
        </Link>
        <a href="/dashboard" className="product-choice">
          <span className="product-choice-dot flow-dot" />
          <span><strong>OttoFlow</strong><small>Операции и экспорт</small></span>
        </a>
      </div>
    </details>
  );
}
