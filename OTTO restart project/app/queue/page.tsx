import type { Metadata } from "next";
import { QueueSchedulerWorkspace } from "@/components/queue-scheduler-workspace";

export const metadata: Metadata = { title: "Очередь и планировщик" };

export default function QueuePage() {
  return <QueueSchedulerWorkspace />;
}
