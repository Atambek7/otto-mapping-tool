"use client";
import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import type { RestartAccount } from "../api/types";

const AccountContext = createContext<{ account: RestartAccount; ready: boolean; choose: (account: RestartAccount) => void } | null>(null);
export function RestartAccountProvider({ children }: { children: ReactNode }) {
  const [account, setAccount] = useState<RestartAccount>("JV");
  const [ready, setReady] = useState(false);
  useEffect(() => {
    try { const saved = localStorage.getItem("ottorestart_account"); if (saved === "JV" || saved === "XL") setAccount(saved); } catch { /* In-memory selection is sufficient when storage is disabled. */ }
    setReady(true);
  }, []);
  function choose(value: RestartAccount) {
    setAccount(value);
    try { localStorage.setItem("ottorestart_account", value); } catch { /* Keep the selected account in memory. */ }
  }
  return <AccountContext.Provider value={{ account, ready, choose }}>{children}</AccountContext.Provider>;
}
export function useRestartAccount() {
  const context = useContext(AccountContext);
  if (!context) throw new Error("RestartAccountProvider is required.");
  return context;
}
