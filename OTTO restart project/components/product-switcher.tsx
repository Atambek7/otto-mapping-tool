import Link from "next/link";
import Image from "next/image";
import { ChevronDown } from "lucide-react";
import logoRed from "@/components/demo/images/otto-logo_red.png";
import logoBrown from "@/components/demo/images/otto_log_brown.png";

export function ProductSwitcher() {
  return (
    <details className="product-switcher product-switcher-centered">
      <summary aria-label="Выбрать продукт">
        <div className="product-brand-center">
          <div className="product-logo-wrap">
            <Image
              src={logoRed}
              alt="OTTO Restart"
              className="brand-logo-img logo-dark"
              width={116}
              height={32}
              priority
            />
            <Image
              src={logoBrown}
              alt="OTTO Restart"
              className="brand-logo-img logo-light"
              width={116}
              height={32}
              priority
            />
          </div>
          <small className="product-tagline">Restart 3/12M</small>
        </div>
        <ChevronDown className="product-chevron" size={14} />
      </summary>
      <div className="product-menu">
        <Link href="/" className="product-choice selected">
          <span className="product-choice-logo">
            <Image
              src={logoRed}
              alt=""
              className="brand-menu-logo logo-dark"
              width={38}
              height={19}
            />
            <Image
              src={logoBrown}
              alt=""
              className="brand-menu-logo logo-light"
              width={38}
              height={19}
            />
          </span>
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
