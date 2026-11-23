import type { Metadata } from "next";
import { DemoEanPoolWorkspace } from "@/components/demo/ean-pool-workspace";
import { EanPoolWorkspace } from "@/components/ean-pool-workspace";
import { RuntimeModeGate } from "@/components/runtime-mode-gate";
import { getOttoRestartMode, isOttoRestartDemoMode } from "@/features/runtime-mode";

export const metadata: Metadata = { title: "Пул новых EAN" };

export default function EanPoolPage() {
  const mode = getOttoRestartMode();
  if (mode === "PRODUCTION" || mode === "TEST") return <EanPoolWorkspace />;
  if (mode === "DEMO" || isOttoRestartDemoMode()) return <section className="page-section">
    <div className="page-heading"><div><div className="eyebrow">ДАННЫЕ</div><h1>Пул новых EAN</h1><p>Просмотр и тестирование пула синтетических идентификаторов.</p></div><span className="phase-chip"><span /> DEMO · GTIN pool не подключён</span></div>
    <DemoEanPoolWorkspace />
  </section>;
  return <RuntimeModeGate title="Пул EAN недоступен" />;
}
