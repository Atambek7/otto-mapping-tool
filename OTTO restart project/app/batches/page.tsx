import type { Metadata } from "next";
import { DemoBatchListWorkspace } from "@/components/demo/batch-list-workspace";

export const metadata: Metadata = { title: "Партии Restart" };

export default function BatchesPage() {
  return <DemoBatchListWorkspace />;
}
