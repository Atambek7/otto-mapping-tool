import type { Metadata } from "next";
import { DemoHistoryWorkspace } from "@/components/demo/history-workspace";

export const metadata: Metadata = { title: "История" };

export default function HistoryPage() {
  return <section className="page-section">
    <div className="page-heading"><div><div className="eyebrow">ДАННЫЕ</div><h1>История OttoRestart</h1><p>Журнал действий в текущей demo-сессии.</p></div><span className="phase-chip"><span /> DEMO · session audit</span></div>
    <DemoHistoryWorkspace />
  </section>;
}
