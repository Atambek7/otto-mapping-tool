import type { Metadata } from "next";
import { DemoSettingsWorkspace } from "@/components/demo/settings-workspace";
import { ProductionSettingsWorkspace } from "@/components/production-settings-workspace";
import { RuntimeModeGate } from "@/components/runtime-mode-gate";
import { getOttoRestartMode, isOttoRestartDemoMode } from "@/features/runtime-mode";

export const metadata: Metadata = {
  title: "Настройки — OTTO Restart",
  description: "Управление языком интерфейса, ключами нейросети и параметрами аккаунта",
};

export default function SettingsPage() {
  const mode = getOttoRestartMode();
  if (mode === "PRODUCTION" || mode === "TEST") return <ProductionSettingsWorkspace />;
  if (mode === "DEMO" || isOttoRestartDemoMode()) return <DemoSettingsWorkspace />;
  return <RuntimeModeGate title="Настройки недоступны" />;
}
