import type { Metadata } from "next";
import { DirectOttoView } from "@/components/catalogs/direct-otto-view";

export const metadata: Metadata = {
  title: "Товары напрямую в ОТТО",
  description: "Товары, отправленные напрямую в ОТТО, с выявленными ошибками 3/12 месяцев",
};

export default function DirectOttoPage() {
  return <DirectOttoView />;
}
