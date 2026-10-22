import type { Metadata } from "next";
import { DemoSettingsWorkspace } from "@/components/demo/settings-workspace";

export const metadata: Metadata = { title: "Настройки" };

export default function SettingsPage() { return <section className="page-section"><div className="page-heading"><div><div className="eyebrow">СИСТЕМА</div><h1>Настройки OttoRestart</h1><p>Ограничения, ценовые правила и параметры demo-режима.</p></div><span className="phase-chip"><span /> DEMO · локальная сессия</span></div><DemoSettingsWorkspace /></section>; }
