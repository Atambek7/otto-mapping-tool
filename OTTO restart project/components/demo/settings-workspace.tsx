"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { FlaskConical, RefreshCw, Save, ShieldCheck } from "lucide-react";
import { ApiState } from "@/components/api-state";
import { DemoWorkspaceClient } from "@/features/demo/client";
import type { DemoSettings } from "@/features/demo/types";
import { useRestartAccount } from "@/features/state/restart-account";
import type { RestartPeriod } from "@/features/api/types";

const client = new DemoWorkspaceClient();
export function DemoSettingsWorkspace() {
  const { account, ready } = useRestartAccount(); const [settings, setSettings] = useState<DemoSettings | null>(null);
  const [defaultPeriod, setDefaultPeriod] = useState<RestartPeriod>("12M"); const [maximum, setMaximum] = useState(100); const [strict, setStrict] = useState(true);
  const [loading, setLoading] = useState(true); const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [notice, setNotice] = useState(""); const [reload, setReload] = useState(0);
  const load = useCallback(async (active: () => boolean) => { setLoading(true); setError(""); try { const result = await client.settings(account); if (active()) { setSettings(result); setDefaultPeriod(result.defaultPeriod); setMaximum(result.maximumDemoBatchItems); setStrict(result.strictAttributeChecks); } } catch (cause) { if (active()) setError(cause instanceof Error ? cause.message : "Не удалось загрузить demo-настройки."); } finally { if (active()) setLoading(false); } }, [account]);
  useEffect(() => { if (!ready) return; let active = true; void load(() => active); return () => { active = false; }; }, [ready, load, reload]);
  async function save(event: FormEvent<HTMLFormElement>) { event.preventDefault(); setBusy(true); setError(""); setNotice(""); try { const next = await client.saveSettings(account, { defaultPeriod, maximumDemoBatchItems: maximum, strictAttributeChecks: strict }); setSettings(next); setNotice("Настройки демонстрационного режима сохранены для текущей сессии."); } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось сохранить настройки."); } finally { setBusy(false); } }
  return <div className="workspace-stack"><div className="demo-mode-banner"><FlaskConical size={19} /><div><strong>Настройки демо-сессии · {account}</strong><span>Изменения живут в памяти сессии браузера и не меняют реальную конфигурацию сервиса.</span></div><button className="demo-button" type="button" disabled={loading || busy} onClick={() => setReload((value) => value + 1)}><RefreshCw size={14} /> Обновить</button></div>
    {loading ? <ApiState kind="loading" label="Загружаем demo-настройки…" /> : null}{error ? <ApiState kind="error" description={error} onRetry={() => setReload((value) => value + 1)} /> : null}{notice ? <p className="success-banner" role="status">{notice}</p> : null}
    {settings ? <form className="workspace-stack" onSubmit={(event) => void save(event)}>
      <section className="surface-card settings-card"><div className="card-heading"><div><h2>Правила тестового импорта</h2><p>Параметры синтетических сценариев для аккаунта {account}.</p></div></div><div className="demo-form-grid"><label className="field-label">Период по умолчанию<select value={defaultPeriod} onChange={(event) => setDefaultPeriod(event.target.value as RestartPeriod)}><option value="3M">3 месяца</option><option value="12M">12 месяцев</option></select><small>Подставляется при демонстрационном создании партии.</small></label><label className="field-label">Максимум строк в demo-партии<input type="number" min={1} max={1000} value={maximum} onChange={(event) => setMaximum(Number(event.target.value))} /><small>Синтетическое ограничение интерфейса.</small></label></div><label className="demo-toggle-row"><span><strong>Строгая проверка тестовых атрибутов</strong><small>Демо-валидатор потребует заполнить условные характеристики карточки.</small></span><input type="checkbox" checked={strict} onChange={(event) => setStrict(event.target.checked)} /></label><div className="demo-form-actions"><span>Последнее изменение: {new Date(settings.updatedAt).toLocaleString("ru-RU")}</span><button className="primary-action button-action" type="submit" disabled={busy}><Save size={14} /> {busy ? "Сохраняем…" : "Сохранить demo-настройки"}</button></div></section>
      <section className="surface-card settings-card"><div className="card-heading"><div><h2>Цены и ограничения</h2><p>Правила, зафиксированные в архитектурном плане.</p></div><ShieldCheck className="muted-icon" size={18} /></div><div className="demo-settings-rules"><div><span>Источник цены</span><strong>{settings.priceRule.source} · mock adapter</strong></div><div><span>JV</span><strong>{settings.priceRule.JV}</strong></div><div><span>XL</span><strong>{settings.priceRule.XL}</strong></div><div><span>Публикация</span><strong className="demo-lock-text">ОТКЛЮЧЕНА · restart_publish_enabled=false</strong></div></div><p className="demo-settings-footnote">Цены в review показывают только синтетический сценарий старого SKU. Настоящие OTTO цены не запрашиваются, а включить публикацию из этих настроек нельзя.</p></section>
    </form> : null}
  </div>;
}
