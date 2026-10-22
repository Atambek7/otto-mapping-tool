"use client";

import { useState } from "react";
import { Check, ChevronDown, Store } from "lucide-react";
import { useRestartAccount } from "@/features/state/restart-account";

type Account = "JV" | "XL";

export function AccountSwitcher() {
  const { account, choose: chooseAccount } = useRestartAccount();
  const [open, setOpen] = useState(false);

  const choose = (nextAccount: Account) => {
    chooseAccount(nextAccount);
    setOpen(false);
  };

  return (
    <div className="account-switcher">
      <button
        type="button"
        className="account-trigger"
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => setOpen((value) => !value)}
      >
        <Store size={15} />
        <span className="account-label">Аккаунт</span>
        <strong>{account}</strong>
        <ChevronDown size={14} />
      </button>
      {open && (
        <div className="account-menu" role="listbox" aria-label="Аккаунт OTTO">
          {(["JV", "XL"] as const).map((option) => (
            <button
              type="button"
              role="option"
              aria-selected={account === option}
              className="account-option"
              key={option}
              onClick={() => choose(option)}
            >
              <span>{option === "JV" ? "JV Möbel" : "XL Möbel"} <small>({option})</small></span>
              {account === option && <Check size={15} />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
