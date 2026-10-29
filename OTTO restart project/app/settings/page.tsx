import type { Metadata } from "next";
import { DemoSettingsWorkspace } from "@/components/demo/settings-workspace";

export const metadata: Metadata = {
  title: "Настройки — OTTO Restart",
  description: "Управление языком интерфейса, ключами нейросети и параметрами аккаунта",
};

export default function SettingsPage() {
  return <DemoSettingsWorkspace />;
}
