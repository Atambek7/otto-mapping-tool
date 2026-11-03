import type { Metadata } from "next";
import { DiscoverView } from "@/components/catalogs/discover-view";

export const metadata: Metadata = {
  title: "Выявить новые 3/12",
  description:
    "Автоматический поиск товаров 3 и 12 месяцев через OTTO API и распределение по источникам",
};

export default function DiscoverPage() {
  return <DiscoverView />;
}

