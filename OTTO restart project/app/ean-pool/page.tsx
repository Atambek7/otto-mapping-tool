import type { Metadata } from "next";
import { DemoEanPoolWorkspace } from "@/components/demo/ean-pool-workspace";

export const metadata: Metadata = { title: "Пул новых EAN" };

export default function EanPoolPage() {
  return <section className="page-section">
    <div className="page-heading"><div><div className="eyebrow">ДАННЫЕ</div><h1>Пул новых EAN</h1><p>Просмотр и тестирование пула синтетических идентификаторов.</p></div><span className="phase-chip"><span /> DEMO · GTIN pool не подключён</span></div>
    <DemoEanPoolWorkspace />
  </section>;
}
