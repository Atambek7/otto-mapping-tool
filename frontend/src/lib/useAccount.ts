"use client";

import { useState, useEffect, useCallback } from "react";

export type AccountType = "JV" | "XL";

export function useAccount() {
  const [account, setAccountState] = useState<AccountType>("JV");

  useEffect(() => {
    // Read initial state
    try {
      const saved = localStorage.getItem("current_account");
      if (saved === "JV" || saved === "XL") {
        setAccountState(saved);
      } else {
        localStorage.setItem("current_account", "JV");
      }
    } catch (e) {
      console.error("Failed to read account from localStorage", e);
    }

    // Listen for custom event and cross-tab storage event
    const handleAccountChange = () => {
      try {
        const current = localStorage.getItem("current_account");
        if (current === "JV" || current === "XL") {
          setAccountState(current);
        }
      } catch (e) {}
    };

    window.addEventListener("account_changed", handleAccountChange);
    window.addEventListener("storage", handleAccountChange);

    return () => {
      window.removeEventListener("account_changed", handleAccountChange);
      window.removeEventListener("storage", handleAccountChange);
    };
  }, []);

  const setAccount = useCallback((newAccount: AccountType) => {
    try {
      localStorage.setItem("current_account", newAccount);
      setAccountState(newAccount);
      window.dispatchEvent(new Event("account_changed"));
    } catch (e) {
      console.error("Failed to set account in localStorage", e);
    }
  }, []);

  return { account, setAccount };
}
