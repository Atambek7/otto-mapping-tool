"use client";

import { Database } from "lucide-react";
import { usePathname } from "next/navigation";
import { getOttoRestartMode, isOttoRestartDemoMode, type OttoRestartDemoTab } from "@/features/runtime-mode";

export function ApiConnectionStatus() {
  const pathname = usePathname();
  const mode = getOttoRestartMode();
  const path = pathname.replace(/^\/restart(?=\/|$)/, "") || "/";
  let demoTab: OttoRestartDemoTab | undefined;
  if (path === "/discover") demoTab = "discover";
  else if (path === "/afterbuy" || path.startsWith("/afterbuy/")) demoTab = "afterbuy";
  else if (path === "/direct-otto") demoTab = "direct-otto";
  else if (path === "/library") demoTab = "library";
  const localDemo = mode === "DEMO" || (demoTab ? isOttoRestartDemoMode(demoTab) : false);
  const label = localDemo ? "Локальный режим" : mode;
  return (
    <div
      className="connection-state"
      title={localDemo ? "Демо-данные локальные; OTTO/Aftercool API не вызываются." : "Режим OttoRestart: " + mode}
    >
      <Database size={13} />
      <span>{label}</span>
    </div>
  );
}
