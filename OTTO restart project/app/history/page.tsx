import type { Metadata } from "next";
import { DemoHistoryWorkspace } from "@/components/demo/history-workspace";
import { RestartHistoryWorkspace } from "@/components/restart-history-workspace";
import { RuntimeModeGate } from "@/components/runtime-mode-gate";
import { getOttoRestartMode, isOttoRestartDemoMode } from "@/features/runtime-mode";

export const metadata: Metadata = { title: "История" };

export default function HistoryPage() {
  const mode = getOttoRestartMode();
  if (mode === "PRODUCTION" || mode === "TEST") return <section className="page-section">
    <div className="page-heading"><div><div className="eyebrow">ДАННЫЕ</div><h1>История OttoRestart</h1><p>События backend отдельно по выбранному аккаунту.</p></div><span className="phase-chip"><span /> API · production</span></div>
    <RestartHistoryWorkspace />
  </section>;
  if (mode !== "DEMO" && !isOttoRestartDemoMode()) return <RuntimeModeGate title="История недоступна" />;
  return <section className="page-section">
    <div className="page-heading"><div><div className="eyebrow">ДАННЫЕ</div><h1>История OttoRestart</h1><p>Журнал действий в текущей demo-сессии.</p></div><span className="phase-chip"><span /> DEMO · session audit</span></div>
    <DemoHistoryWorkspace />
  </section>;
}
