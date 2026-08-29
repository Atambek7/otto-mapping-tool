"use client";

import * as React from "react";
import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { cn } from "@/lib/utils";

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => setMounted(true), []);

  if (!mounted) {
    return (
      <div className="flex items-center gap-1 p-1 rounded-full bg-slate-100 dark:bg-slate-950/50 border border-slate-200 dark:border-slate-800 w-[76px] h-[42px]">
      </div>
    );
  }

  const isDark = theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);

  return (
    <div className="flex items-center gap-1 p-1 rounded-full bg-slate-100 dark:bg-slate-950/50 border border-slate-200 dark:border-slate-800">
      <button
        onClick={() => setTheme("light")}
        className={cn(
          "flex items-center justify-center w-8 h-8 rounded-full transition-all",
          !isDark ? "bg-white text-indigo-600 shadow-sm" : "text-slate-400 hover:text-slate-200"
        )}
        title="Light Mode"
      >
        <Sun className="w-4 h-4" />
      </button>
      <button
        onClick={() => setTheme("dark")}
        className={cn(
          "flex items-center justify-center w-8 h-8 rounded-full transition-all",
          isDark ? "bg-indigo-600 text-white shadow-sm" : "text-slate-500 hover:text-slate-700"
        )}
        title="Dark Mode"
      >
        <Moon className="w-4 h-4" />
      </button>
    </div>
  );
}
