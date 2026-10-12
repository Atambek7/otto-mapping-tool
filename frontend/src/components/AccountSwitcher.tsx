"use client";

import { useState, useRef, useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAccount, AccountType } from "@/lib/useAccount";
import { Building, ChevronDown, Check } from "lucide-react";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";
import { fetchWithAuth } from "@/lib/api";
import { toast } from "sonner";

export function AccountSwitcher() {
  const { account, setAccount } = useAccount();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const { t } = useTranslation();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const selectAccount = async (newAcc: AccountType) => {
    if (newAcc === account) {
      setOpen(false);
      return;
    }

    setOpen(false);

    // Check if user is currently inside a factory-scoped route
    const match = (pathname || "").match(/^\/dashboard\/(factories|validation)\/([^/]+)/);
    if (!match) {
      // Standard switch for global pages
      setAccount(newAcc);
      return;
    }

    const routePrefix = match[1]; // "factories" or "validation"
    const currentFid = match[2];

    // 1. Try resolving twin factory from local cache (0ms instant)
    let currentFactoryName = "";
    try {
      const currentListRaw = localStorage.getItem(`otto_factories_list_${account}`);
      const targetListRaw = localStorage.getItem(`otto_factories_list_${newAcc}`);
      const currentList = currentListRaw ? JSON.parse(currentListRaw) : [];
      const targetList = targetListRaw ? JSON.parse(targetListRaw) : [];

      const currentFactory = currentList.find((f: any) => String(f.id) === String(currentFid));
      if (currentFactory?.name) {
        currentFactoryName = currentFactory.name;
        const normName = currentFactory.name.toLowerCase().trim();
        const twin = targetList.find(
          (f: any) => f.name?.toLowerCase().trim() === normName && (f.kind === currentFactory.kind || f.kind === "lister")
        );
        if (twin?.id) {
          setAccount(newAcc);
          router.replace(`/dashboard/${routePrefix}/${twin.id}`);
          return;
        }
      }
    } catch (e) {
      console.error("Local twin lookup error:", e);
    }

    // 2. Fallback to backend twin resolver
    try {
      const res = await fetchWithAuth(
        `/aftercool/twin-factory?factory_id=${currentFid}&source_account=${account}&target_account=${newAcc}`
      );
      if (res.ok) {
        const data = await res.json();
        if (data.target_factory_id) {
          setAccount(newAcc);
          router.replace(`/dashboard/${routePrefix}/${data.target_factory_id}`);
          return;
        }
      }
    } catch (e) {
      console.error("Backend twin lookup error:", e);
    }

    // 3. Fallback when factory does not exist in target account
    setAccount(newAcc);
    router.replace("/dashboard/factories");
    const nameLabel = currentFactoryName ? `«${currentFactoryName}» ` : "";
    toast.info(`Фабрика ${nameLabel}на данный момент отсутствует в аккаунте ${newAcc}. Открыт список фабрик.`);
  };

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(!open)}
        className={cn(
          "flex items-center gap-2 px-2.5 py-1.5 text-xs font-medium text-slate-700 dark:text-slate-200 bg-white dark:bg-[#111827] rounded-lg border border-slate-200 dark:border-slate-800 shadow-xs hover:bg-slate-50 dark:hover:bg-slate-800/80 transition-colors cursor-pointer focus:outline-none focus:ring-2 focus:ring-indigo-500/20",
          open && "border-slate-300 dark:border-slate-700 bg-slate-50 dark:bg-slate-800"
        )}
      >
        <Building className="w-3.5 h-3.5 text-indigo-600 dark:text-indigo-400" />
        <span className="text-slate-500 dark:text-slate-400">{t("account_switcher.account_label")}</span>
        <span className="px-1.5 py-0.5 rounded text-[11px] font-semibold bg-indigo-50 dark:bg-indigo-950/60 text-indigo-700 dark:text-indigo-300 border border-indigo-100 dark:border-indigo-900/40">
          {account}
        </span>
        <ChevronDown
          className={cn(
            "w-3.5 h-3.5 text-slate-400 transition-transform duration-150",
            open && "rotate-180"
          )}
        />
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-1.5 w-52 bg-white dark:bg-[#111827] border border-slate-200 dark:border-slate-800 rounded-xl shadow-lg shadow-slate-900/5 dark:shadow-black/40 overflow-hidden z-50 py-1 animate-in fade-in-50 zoom-in-95 duration-100">
          <div className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500 border-b border-slate-100 dark:border-slate-800">
            {t("account_switcher.switch_workspace")}
          </div>

          <div className="p-1 space-y-0.5">
            <button
              onClick={() => selectAccount("JV")}
              className={cn(
                "w-full flex items-center justify-between px-2.5 py-2 text-xs font-medium rounded-lg transition-colors cursor-pointer",
                account === "JV"
                  ? "bg-indigo-50/80 dark:bg-indigo-950/40 text-indigo-700 dark:text-indigo-300 font-semibold"
                  : "text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800/60"
              )}
            >
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-indigo-500" />
                <span>JV Möbel (JV)</span>
              </div>
              {account === "JV" && <Check className="w-3.5 h-3.5 text-indigo-600 dark:text-indigo-400" />}
            </button>

            <button
              onClick={() => selectAccount("XL")}
              className={cn(
                "w-full flex items-center justify-between px-2.5 py-2 text-xs font-medium rounded-lg transition-colors cursor-pointer",
                account === "XL"
                  ? "bg-indigo-50/80 dark:bg-indigo-950/40 text-indigo-700 dark:text-indigo-300 font-semibold"
                  : "text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800/60"
              )}
            >
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-purple-500" />
                <span>XL Möbel (XL)</span>
              </div>
              {account === "XL" && <Check className="w-3.5 h-3.5 text-indigo-600 dark:text-indigo-400" />}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
