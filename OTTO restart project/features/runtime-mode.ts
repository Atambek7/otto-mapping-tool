export type OttoRestartMode = "LEGACY_READ_ONLY" | "DEMO" | "PRODUCTION" | "TEST";
export type OttoRestartDemoTab = "discover" | "afterbuy" | "direct-otto" | "library";

const SUPPORTED_MODES: OttoRestartMode[] = ["LEGACY_READ_ONLY", "DEMO", "PRODUCTION", "TEST"];
const SUPPORTED_DEMO_TABS: OttoRestartDemoTab[] = ["discover", "afterbuy", "direct-otto", "library"];

/** The UI fails closed unless DEMO is explicitly selected at build/runtime config. */
export function getOttoRestartMode(): OttoRestartMode {
  const configured = process.env.NEXT_PUBLIC_OTTORESTART_MODE?.trim().toUpperCase();
  return SUPPORTED_MODES.includes(configured as OttoRestartMode)
    ? (configured as OttoRestartMode)
    : "LEGACY_READ_ONLY";
}

export function isOttoRestartDemoMode(tab?: OttoRestartDemoTab): boolean {
  const mode = getOttoRestartMode();
  if (mode === "DEMO") return true;
  if (mode !== "LEGACY_READ_ONLY" || !tab || !SUPPORTED_DEMO_TABS.includes(tab)) return false;

  const configuredTabs = (process.env.NEXT_PUBLIC_OTTORESTART_DEMO_TABS ?? "")
    .split(",")
    .map((value) => value.trim().toLowerCase());
  return configuredTabs.includes(tab);
}
