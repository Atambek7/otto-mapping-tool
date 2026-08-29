"use client";

import { useEffect, useState } from "react";
import { fetchWithAuth } from "@/lib/api";
import { useRouter, useParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { LayoutDashboard, LogOut, Check, ChevronsUpDown, RefreshCw, FolderOpen } from "lucide-react";
import { cn } from "@/lib/utils";

export function TopBar() {
  const [factories, setFactories] = useState<{ id: string; name: string; account: string; kind: string }[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const router = useRouter();
  const params = useParams();
  const factoryId = params?.factory_id as string | undefined;

  const loadFactories = async () => {
    setLoading(true);
    setLoadError(false);
    try {
      const res = await fetchWithAuth("/aftercool/factories");
      if (res.ok) {
        const data = await res.json();
        const all = Array.isArray(data) ? data : data?.items || [];
        // Only JV + lister factories contain correctly mapped data
        const filtered = all.filter(
          (f: { account: string; kind: string }) => f.account === "JV" && f.kind === "lister"
        );
        setFactories(filtered);
      }
    } catch (e) {
      console.error(e);
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadFactories();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleLogout = () => {
    localStorage.removeItem("token");
    router.push("/login");
  };

  const selectedFactory = factories.find((f) => f.id === factoryId) || { id: factoryId, name: factoryId };

  return (
    <header className="sticky top-0 z-50 w-full border-b border-slate-200 bg-white/80 backdrop-blur-md dark:bg-zinc-950/80 dark:border-zinc-800">
      <div className="flex h-16 items-center justify-between px-6 sm:px-10 max-w-7xl mx-auto">
        
        {/* Left section: Logo & Factory Selector */}
        <div className="flex items-center space-x-6">
          <div 
            className="flex items-center space-x-2 cursor-pointer"
            onClick={() => router.push("/dashboard")}
          >
            <div className="p-2 bg-indigo-50 rounded-lg dark:bg-indigo-900/30">
              <LayoutDashboard className="w-5 h-5 text-indigo-600 dark:text-indigo-400" />
            </div>
            <span className="font-semibold text-lg hidden sm:inline-block">Otto Mapping</span>
          </div>

          <div className="h-6 w-px bg-slate-200 dark:bg-zinc-800 hidden sm:block" />

          <div className="flex items-center space-x-2">
            <Popover open={open} onOpenChange={setOpen}>
              <PopoverTrigger
                render={
                  <Button
                    variant="outline"
                    role="combobox"
                    className="w-[250px] justify-between shadow-sm bg-slate-50 border-slate-200 hover:bg-slate-100 dark:bg-zinc-900 dark:border-zinc-800"
                  />
                }
              >
                <div className="flex items-center truncate">
                  <FolderOpen className="mr-2 h-4 w-4 text-slate-400 shrink-0" />
                  <span className="truncate">
                    {factoryId 
                      ? (selectedFactory.name || selectedFactory.id) 
                      : "Выберите фабрику..."}
                  </span>
                </div>
                <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
              </PopoverTrigger>
              <PopoverContent className="w-[300px] p-0" align="start">
                <Command>
                  <CommandInput placeholder="Поиск фабрики..." />
                  <CommandList>
                    <CommandEmpty>
                    {loadError ? "Ошибка загрузки. Нажмите ↻" : "Ничего не найдено."}
                  </CommandEmpty>
                    <CommandGroup>
                      {factories.map((factory) => (
                        <CommandItem
                          key={factory.id}
                          value={`${factory.id} ${factory.name || ""}`}
                          onSelect={() => {
                            router.push(`/dashboard/${factory.id}`);
                            setOpen(false);
                          }}
                        >
                          <Check
                            className={cn(
                              "mr-2 h-4 w-4",
                              factoryId === factory.id ? "opacity-100" : "opacity-0"
                            )}
                          />
                          <div className="flex flex-col">
                            <span>{factory.name || factory.id}</span>
                            <span className="text-xs text-slate-500">ID: {factory.id}</span>
                          </div>
                        </CommandItem>
                      ))}
                    </CommandGroup>
                  </CommandList>
                </Command>
              </PopoverContent>
            </Popover>

            <Button
              variant="ghost"
              size="icon"
              onClick={loadFactories}
              disabled={loading}
              title="Обновить список фабрик"
              className="text-slate-500 hover:text-slate-900"
            >
              <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
            </Button>
          </div>
        </div>

        {/* Right section: Actions */}
        <div className="flex items-center">
          <Button 
            variant="ghost" 
            onClick={handleLogout}
            className="text-slate-500 hover:text-slate-900"
          >
            <LogOut className="h-4 w-4 sm:mr-2" />
            <span className="hidden sm:inline-block">Выйти</span>
          </Button>
        </div>
      </div>
    </header>
  );
}
