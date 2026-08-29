"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Package, CheckCircle, AlertTriangle, AlertCircle,
  UploadCloud, Clock, PieChart, Loader2
} from "lucide-react";
import { fetchWithAuth } from "@/lib/api";

interface Stats {
  total_products: number;
  mapped: number;
  need_attributes: number;
  error: number;
  ready: number;
  unmapped: number;
}

export default function DashboardPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchWithAuth("/stats")
      .then(res => res.json())
      .then(data => setStats(data))
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full min-h-[50vh]">
        <Loader2 className="w-8 h-8 animate-spin text-indigo-500" />
      </div>
    );
  }

  if (!stats) return null;

  // For the donut chart, we show the mutually exclusive statuses of categorized items:
  // Mapped, Need Attributes, Error, Ready. 
  // (We skip unmapped to show progress of what is in the pipeline, or include it if we want the whole pie to be total_products).
  // The mockup donut shows Mapped, Need Attributes, Errors, Ready. 
  // User requested to remove "Mapped" (only category). We will show "Unmapped" instead to make the donut 100% of Total.
  const chartData = [
    { label: "Unmapped", value: stats.unmapped, color: "#94a3b8" }, // Slate 400
    { label: "Need Attributes", value: stats.need_attributes, color: "#f59e0b" }, // Amber 500
    { label: "Errors", value: stats.error, color: "#ef4444" }, // Red 500
    { label: "Ready for Export", value: stats.ready, color: "#4f46e5" } // Indigo 600
  ];

  const totalInPipeline = stats.total_products;
  const readyPercent = stats.total_products > 0
    ? Math.round((stats.ready / stats.total_products) * 100)
    : 0;

  let currentOffset = 0;
  const circumference = 2 * Math.PI * 40; // 251.327

  return (
    <div className="space-y-8 animate-in fade-in duration-500 pb-10">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-zinc-100">Dashboard</h1>
        <p className="text-slate-500 mt-1">Overview of your Otto mapping progress.</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
        {/* Total Products */}
        <Card className="shadow-sm border-slate-200/60 dark:border-zinc-800/60">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-slate-600 dark:text-zinc-400">Total Products</CardTitle>
            <Package className="w-4 h-4 text-slate-400" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold dark:text-zinc-100">{stats.total_products}</div>
            <p className="text-xs text-slate-400 mt-1">All imported products</p>
          </CardContent>
        </Card>

        {/* Unmapped */}
        <Card className="shadow-sm border-slate-200/60 dark:border-zinc-800/60">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-slate-600 dark:text-zinc-400">Unmapped</CardTitle>
            <CheckCircle className="w-4 h-4 text-slate-400" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-slate-600 dark:text-slate-400">{stats.unmapped}</div>
            <p className="text-xs text-slate-400 mt-1">Products waiting processing</p>
          </CardContent>
        </Card>

        {/* Need Attributes */}
        <Card className="shadow-sm border-slate-200/60 dark:border-zinc-800/60">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-slate-600 dark:text-zinc-400">Need Attributes</CardTitle>
            <AlertCircle className="w-4 h-4 text-amber-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-amber-600 dark:text-amber-400">{stats.need_attributes}</div>
            <p className="text-xs text-slate-400 mt-1">Missing required attributes</p>
          </CardContent>
        </Card>

        {/* Errors */}
        <Card className="shadow-sm border-slate-200/60 dark:border-zinc-800/60">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-slate-600 dark:text-zinc-400">Errors</CardTitle>
            <AlertTriangle className="w-4 h-4 text-red-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-red-600 dark:text-red-400">{stats.error}</div>
            <p className="text-xs text-slate-400 mt-1">Products with errors</p>
          </CardContent>
        </Card>

        {/* Ready */}
        <Card className="shadow-sm border-slate-200/60 dark:border-zinc-800/60 bg-indigo-50/50 dark:bg-indigo-950/20">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-indigo-700 dark:text-indigo-400">Ready for Export</CardTitle>
            <UploadCloud className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-indigo-700 dark:text-indigo-400">{stats.ready}</div>
            <p className="text-xs text-indigo-500/70 dark:text-indigo-500/80 mt-1">Products ready to export</p>
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

        {/* Recent Activity (Aggregated) */}
        <div className="bg-white dark:bg-zinc-900 rounded-2xl border border-slate-200/60 dark:border-zinc-800 shadow-sm p-6 flex flex-col">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-lg font-semibold text-slate-800 dark:text-zinc-100 flex items-center gap-2">
              <Clock className="w-5 h-5 text-indigo-500" /> Recent Activity
            </h2>
          </div>

          <div className="space-y-6 flex-1">
            <div className="flex items-start gap-4">
              <div className="p-2 bg-slate-50 dark:bg-slate-500/10 rounded-full mt-0.5">
                <CheckCircle className="w-4 h-4 text-slate-500" />
              </div>
              <div>
                <p className="text-sm font-medium text-slate-700 dark:text-zinc-200">{stats.unmapped} unmapped products</p>
                <p className="text-xs text-slate-500 dark:text-zinc-500 mt-1">Waiting in pipeline</p>
              </div>
            </div>

            <div className="flex items-start gap-4">
              <div className="p-2 bg-amber-50 dark:bg-amber-500/10 rounded-full mt-0.5">
                <AlertCircle className="w-4 h-4 text-amber-500" />
              </div>
              <div>
                <p className="text-sm font-medium text-slate-700 dark:text-zinc-200">{stats.need_attributes} products need attributes</p>
                <p className="text-xs text-slate-500 dark:text-zinc-500 mt-1">Missing required fields</p>
              </div>
            </div>

            <div className="flex items-start gap-4">
              <div className="p-2 bg-red-50 dark:bg-red-500/10 rounded-full mt-0.5">
                <AlertTriangle className="w-4 h-4 text-red-500" />
              </div>
              <div>
                <p className="text-sm font-medium text-slate-700 dark:text-zinc-200">{stats.error} validation errors detected</p>
                <p className="text-xs text-slate-500 dark:text-zinc-500 mt-1">Please review and fix</p>
              </div>
            </div>

            <div className="flex items-start gap-4">
              <div className="p-2 bg-indigo-50 dark:bg-indigo-500/10 rounded-full mt-0.5">
                <UploadCloud className="w-4 h-4 text-indigo-500" />
              </div>
              <div>
                <p className="text-sm font-medium text-slate-700 dark:text-zinc-200">{stats.ready} products ready to export</p>
                <p className="text-xs text-slate-500 dark:text-zinc-500 mt-1">Passed all validations</p>
              </div>
            </div>
          </div>
        </div>

        {/* Mapping Progress Donut Chart */}
        <div className="bg-white dark:bg-zinc-900 rounded-2xl border border-slate-200/60 dark:border-zinc-800 shadow-sm p-6">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-lg font-semibold text-slate-800 dark:text-zinc-100 flex items-center gap-2">
              <PieChart className="w-5 h-5 text-indigo-500" /> Mapping Progress
            </h2>
          </div>

          <div className="flex flex-col sm:flex-row items-center gap-8 justify-center h-full pb-8">
            {/* Donut */}
            <div className="relative w-48 h-48 flex-shrink-0">
              <svg width="192" height="192" viewBox="0 0 100 100" className="transform -rotate-90">
                {/* Background circle */}
                <circle cx="50" cy="50" r="40" fill="transparent" stroke="currentColor" className="text-slate-100 dark:text-zinc-800" strokeWidth="12" />

                {/* Segments */}
                {totalInPipeline > 0 && chartData.map((item, i) => {
                  if (item.value === 0) return null;
                  const strokeDasharray = `${(item.value / totalInPipeline) * circumference} ${circumference}`;
                  const strokeDashoffset = -currentOffset;
                  currentOffset += (item.value / totalInPipeline) * circumference;
                  return (
                    <circle
                      key={item.label}
                      cx="50"
                      cy="50"
                      r="40"
                      fill="transparent"
                      stroke={item.color}
                      strokeWidth="12"
                      strokeDasharray={strokeDasharray}
                      strokeDashoffset={strokeDashoffset}
                      className="transition-all duration-1000 ease-out"
                    />
                  );
                })}
              </svg>
              <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                <span className="text-3xl font-bold text-slate-800 dark:text-zinc-100">{readyPercent}%</span>
                <span className="text-xs text-slate-500 dark:text-zinc-400 mt-1">Ready</span>
              </div>
            </div>

            {/* Legend */}
            <div className="flex-1 w-full space-y-4">
              {chartData.map((item) => {
                const percent = totalInPipeline > 0 ? Math.round((item.value / totalInPipeline) * 100) : 0;
                return (
                  <div key={item.label} className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: item.color }} />
                      <span className="text-sm text-slate-600 dark:text-zinc-400">{item.label}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-slate-800 dark:text-zinc-200">{item.value}</span>
                      <span className="text-xs text-slate-400 w-10 text-right">({percent}%)</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}
