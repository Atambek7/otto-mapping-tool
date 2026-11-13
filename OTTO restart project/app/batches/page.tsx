import type { Metadata } from "next";
import { DemoBatchListWorkspace } from "@/components/demo/batch-list-workspace";
import { BatchImportWorkspace } from "@/components/batch-import-workspace";
import { RuntimeModeGate } from "@/components/runtime-mode-gate";
import { getOttoRestartMode, isOttoRestartDemoMode } from "@/features/runtime-mode";

export const metadata: Metadata = { title: "Партии Restart" };

export default function BatchesPage() {
  const mode = getOttoRestartMode();
  if (mode === "PRODUCTION" || mode === "TEST") return <BatchImportWorkspace />;
  if (mode === "DEMO" || isOttoRestartDemoMode()) return <DemoBatchListWorkspace />;
  return <RuntimeModeGate title="Партии недоступны" />;
}
