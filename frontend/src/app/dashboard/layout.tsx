import { Sidebar } from "@/components/Sidebar";
import { ThemeToggle } from "@/components/ThemeToggle";
import { CacheCleaner } from "@/components/CacheCleaner";
import { CommandPalette, SearchHint } from "@/components/CommandPalette";

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {

  return (
    <div className="flex min-h-screen bg-slate-50/50 dark:bg-background font-sans">
      <CacheCleaner />
      <CommandPalette />
      <Sidebar />
      <main className="flex-1 overflow-x-hidden flex flex-col h-screen">
        {/* Global Header */}
        <div className="h-16 px-8 flex items-center justify-end shrink-0 gap-4">
          <SearchHint />
          <ThemeToggle />
        </div>
        <div className="flex-1 flex flex-col overflow-y-auto px-8 pb-8 relative">
          <div className="w-[90%] max-w-[1800px] mx-auto flex-1 w-full flex flex-col">
            {children}
          </div>
        </div>
      </main>
    </div>
  );
}
