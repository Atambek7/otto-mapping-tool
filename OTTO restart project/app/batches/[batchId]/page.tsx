import type { Metadata } from "next";
import { DemoBatchDetailsWorkspace } from "@/components/demo/batch-details-workspace";
import { BatchDetailsWorkspace } from "@/components/batch-details-workspace";
import { RuntimeModeGate } from "@/components/runtime-mode-gate";
import { getOttoRestartMode, isOttoRestartDemoMode } from "@/features/runtime-mode";

export const metadata: Metadata = { title: "Детали партии" };

export default async function BatchDetailsPage({ params }: { params: Promise<{ batchId: string }> }) {
  const { batchId } = await params;
  const mode = getOttoRestartMode();
  if (mode === "PRODUCTION" || mode === "TEST") return <BatchDetailsWorkspace batchId={batchId} />;
  if (mode === "DEMO" || isOttoRestartDemoMode()) return <DemoBatchDetailsWorkspace batchId={batchId} />;
  return <RuntimeModeGate title="Детали партии недоступны" />;
}
