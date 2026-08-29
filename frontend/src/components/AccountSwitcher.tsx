"use client";

import { useAccount } from "@/lib/useAccount";
import { Building2, ChevronDown } from "lucide-react";
import { useState, useRef, useEffect } from "react";

export function AccountSwitcher() {
  const { account, setAccount } = useAccount();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 w-full px-3 py-2 text-sm font-medium text-slate-700 dark:text-slate-200 bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 shadow-sm hover:bg-slate-50 dark:hover:bg-slate-700/50 transition-colors"
      >
        <Building2 className="w-4 h-4 text-indigo-500" />
        <span className="flex-1 text-left">Аккаунт: <strong>{account}</strong></span>
        <ChevronDown className={`w-4 h-4 text-slate-400 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div className="absolute top-full left-0 w-full mt-1 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-lg overflow-hidden z-50">
          <button
            onClick={() => { setAccount("JV"); setOpen(false); }}
            className={`w-full text-left px-4 py-2.5 text-sm font-medium transition-colors ${account === "JV" ? "bg-indigo-50 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-400" : "text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700/50"}`}
          >
            JV Account
          </button>
        </div>
      )}
    </div>
  );
}
