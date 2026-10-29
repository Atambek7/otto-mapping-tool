import type { Metadata } from "next";
import { ReviewWorkspace } from "@/components/restart-review/review-workspace";

export const metadata: Metadata = { title: "Кандидаты 3/12M" };

export default function ReviewPage() {
  return <ReviewWorkspace />;
}
