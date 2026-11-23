import type { Metadata } from "next";
import { ReviewWorkspace } from "@/components/restart-review/review-workspace";
import { ProductionOperationsWorkspace } from "@/components/production-operations-workspace";
import { RuntimeModeGate } from "@/components/runtime-mode-gate";
import { getOttoRestartMode, isOttoRestartDemoMode } from "@/features/runtime-mode";

export const metadata: Metadata = { title: "Кандидаты 3/12M" };

export default function ReviewPage() {
  const mode = getOttoRestartMode();
  if (mode === "PRODUCTION" || mode === "TEST") return <ProductionOperationsWorkspace />;
  if (mode === "DEMO" || isOttoRestartDemoMode()) return <ReviewWorkspace />;
  return <RuntimeModeGate title="Проверка недоступна" />;
}
