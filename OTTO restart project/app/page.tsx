import type { Metadata } from "next";
import { OverviewWorkspace } from "@/components/demo/overview-workspace";

export const metadata: Metadata = { title: "Обзор OttoRestart · DEMO" };
export default function OverviewPage() { return <OverviewWorkspace />; }
