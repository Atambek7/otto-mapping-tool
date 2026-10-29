import type { Metadata } from "next";
import { FactoryListView } from "@/components/catalogs/factory-list-view";

export const metadata: Metadata = {
  title: "Товары Afterbuy",
  description: "Каталоги поставщиков Aftercool с выявленными товарами 3/12 месяцев",
};

export default function AfterbuyPage() {
  return <FactoryListView />;
}
