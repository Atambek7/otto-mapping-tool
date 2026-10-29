import type { Metadata } from "next";
import { DemoBatchDetailsWorkspace } from "@/components/demo/batch-details-workspace";

export const metadata: Metadata = { title: "Детали партии" };

export default async function BatchDetailsPage({ params }: { params: Promise<{ batchId: string }> }) {
  const { batchId } = await params;
  return <DemoBatchDetailsWorkspace batchId={batchId} />;
}
