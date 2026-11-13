import type { Metadata } from "next";
import { QualityControlWorkspace } from "@/components/quality-control-workspace";
import { ProductionOperationsWorkspace } from "@/components/production-operations-workspace";
import { RuntimeModeGate } from "@/components/runtime-mode-gate";
import { getOttoRestartMode, isOttoRestartDemoMode } from "@/features/runtime-mode";

export const metadata: Metadata = { title: "Quality Control" };
export default function QualityControlPage() {
  const mode = getOttoRestartMode();
  if (mode === "PRODUCTION" || mode === "TEST") return <ProductionOperationsWorkspace view="quality" />;
  if (mode === "DEMO" || isOttoRestartDemoMode()) return <QualityControlWorkspace />;
  return <RuntimeModeGate title="Quality Control недоступен" />;
}
