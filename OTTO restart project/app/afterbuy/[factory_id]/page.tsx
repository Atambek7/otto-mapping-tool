import type { Metadata } from "next";
import { FactoryProductsView } from "@/components/catalogs/factory-products-view";

export const metadata: Metadata = {
  title: "Товары фабрики · Afterbuy",
  description: "Товары 3/12M выбранной фабрики",
};

interface PageProps {
  params: Promise<{ factory_id: string }>;
}

export default async function FactoryDetailPage({ params }: PageProps) {
  const { factory_id } = await params;
  return <FactoryProductsView factoryId={factory_id} />;
}
