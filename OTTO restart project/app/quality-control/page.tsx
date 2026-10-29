import type { Metadata } from "next";
import { QualityControlWorkspace } from "@/components/quality-control-workspace";

export const metadata: Metadata = { title: "Quality Control" };
export default function QualityControlPage() { return <QualityControlWorkspace />; }
