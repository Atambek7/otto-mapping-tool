"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Check, Clock, Globe, RefreshCw, Settings2, ShieldCheck, Sparkles, User } from "lucide-react";
import { ApiState } from "@/components/api-state";
import { RestartApiClient } from "@/features/api/client";
import type { RestartSettings } from "@/features/api/types";
import { useRestartAccount } from "@/features/state/restart-account";
import { TIMEZONE_OPTIONS, useRestartTimezone } from "@/features/state/restart-timezone";
import { setAppLanguage, useTranslation } from "@/features/i18n";
import "./demo/settings.css";
import "./production-settings-workspace.css";

function SettingsCard({ icon, title, description, children }: { icon: ReactNode; title: string; description: string; children: ReactNode }) {
  return <section className="otto-settings-card"><div className="otto-card-header"><div className="otto-card-header-left">
    <div className="otto-card-icon-box icon-accent">{icon}</div><div className="otto-card-title-wrap"><h2>{title}</h2><p>{description}</p></div>
  </div></div><div className="otto-card-body">{children}</div></section>;
}

export function ProductionSettingsWorkspace() {
  const { account, ready, choose } = useRestartAccount();
  const { timezone, setTimezone, resolvedTimezone } = useRestartTimezone();
  const { i18n } = useTranslation();
  const en = i18n.language === "en";
  const text = (ru: string, english: string) => en ? english : ru;
  const [settings, setSettings] = useState<RestartSettings | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [now, setNow] = useState<Date | null>(null);
  const client = useMemo(() => new RestartApiClient(), []);

  useEffect(() => {
    const tick = () => setNow(new Date()); tick();
    const interval = setInterval(tick, 1000);
    return () => clearInterval(interval);
  }, []);
  useEffect(() => {
    if (!ready) return;
    const controller = new AbortController();
    setLoading(true); setError(""); setSettings(null);
    client.getSettings(account, controller.signal).then((value) => {
      if (!controller.signal.aborted) setSettings(value);
    }).catch((cause) => {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Не удалось загрузить настройки.");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [account, client, ready, reload]);

  const configured = (value: boolean) => value ? text("Настроено", "Configured") : text("Не настроено", "Not configured");
  const enabled = (value: boolean) => value ? text("Включено", "Enabled") : text("Выключено", "Disabled");

  return <section className="otto-settings-container production-settings">
    <header className="otto-settings-header production-settings-heading"><div>
      <p className="otto-settings-eyebrow">{text("ПАРАМЕТРЫ И ДОСТУП", "PREFERENCES & ACCESS")}</p>
      <h1 className="otto-settings-title">{text("Настройки OttoRestart", "OttoRestart settings")}</h1>
      <p className="otto-settings-subtitle">{text("Интерфейс, аккаунт и действующие правила обработки товаров", "Interface, account and current product processing rules")}</p>
    </div><button type="button" className="catalog-action-btn" onClick={() => setReload((value) => value + 1)} disabled={loading}><RefreshCw size={15} className={loading ? "spin" : ""} />{text("Обновить", "Refresh")}</button></header>
    <SettingsCard icon={<Globe size={18} />} title={text("Язык интерфейса", "Interface language")} description={text("Применяется сразу в этом браузере", "Applied immediately in this browser")}>
      <div className="otto-lang-grid">{(["ru", "en"] as const).map((language) => <button key={language} type="button" aria-pressed={language === (en ? "en" : "ru")} className={`otto-lang-option ${language === (en ? "en" : "ru") ? "lang-selected" : ""}`} onClick={() => setAppLanguage(language)}>
        <span className="otto-lang-content"><span className="otto-lang-flag">{language.toUpperCase()}</span><span className="otto-lang-name">{language === "ru" ? "Русский" : "English"}</span></span><span className="otto-lang-radio"><Check size={13} /></span>
      </button>)}</div>
    </SettingsCard>
    <SettingsCard icon={<Clock size={18} />} title={text("Часовой пояс", "Timezone")} description={text("Для времени событий и истории операций", "For event and operation history timestamps")}>
      <label className="production-settings-field">{text("Отображать время в", "Display time in")}<select value={timezone} onChange={(event) => setTimezone(event.target.value)}>{TIMEZONE_OPTIONS.map((option) => <option key={option.id} value={option.id}>{option.name} · {option.offsetName}</option>)}</select></label>
      <p className="production-settings-note">{now ? new Intl.DateTimeFormat(en ? "en-GB" : "ru-RU", { dateStyle: "long", timeStyle: "medium", timeZone: resolvedTimezone }).format(now) : "—"} · {resolvedTimezone}</p>
    </SettingsCard>
    <SettingsCard icon={<User size={18} />} title={text("Аккаунт и доступ", "Account & access")} description={text("Выбранный аккаунт применяется ко всем действиям OttoRestart", "The selected account applies to all OttoRestart actions")}>
      <div className="production-settings-account"><span className="otto-profile-avatar">{account}</span><div><strong>{settings?.operator.id || text("Авторизованный оператор", "Authenticated operator")}</strong><p className="production-settings-note">{settings?.mode || "PRODUCTION"} · {settings?.operator.accounts.join(" / ") || "—"}</p></div></div>
      <div className="otto-lang-grid">{(["JV", "XL"] as const).map((value) => <button type="button" key={value} className={`otto-lang-option ${account === value ? "lang-selected" : ""}`} aria-pressed={account === value} disabled={settings ? !settings.operator.accounts.includes(value) : !ready} onClick={() => choose(value)}><strong>{value === "JV" ? "JV Möbel" : "XL Möbel"}</strong><span className="otto-lang-radio"><Check size={13} /></span></button>)}</div>
    </SettingsCard>
    {loading ? <ApiState kind="loading" label={text("Читаем конфигурацию сервера…", "Loading server configuration…")} /> : null}
    {error ? <ApiState kind="error" description={error} onRetry={() => setReload((value) => value + 1)} /> : null}
    {settings ? <>
      <SettingsCard icon={<ShieldCheck size={18} />} title={text("Подключения", "Connections")} description={text("Проверяется конфигурация сервера; внешние запросы не выполняются", "Server configuration only; no provider requests are made")}>
        <dl className="production-settings-values"><div><dt>OTTO {account}</dt><dd>{configured(settings.otto.configured)} · {text("чтение", "reads")}: {enabled(settings.otto.readEnabled)}</dd></div><div><dt>Aftercool</dt><dd>{configured(settings.aftercool.configured)} · {text("чтение", "reads")}: {enabled(settings.aftercool.readEnabled)}</dd></div></dl>
      </SettingsCard>
      <SettingsCard icon={<Sparkles size={18} />} title={text("Искусственный интеллект", "Artificial intelligence")} description={text("Названия, категории и характеристики по фото", "Titles, categories and image attribute suggestions")}>
        <div className="production-settings-status"><span className={`otto-status-badge ${settings.ai.available ? "badge-active" : "badge-warning"}`}>{settings.ai.available ? text("Готов к использованию", "Ready to use") : configured(settings.ai.configured)}</span><span>{text("Модель", "Model")}: <strong>{settings.ai.model || "—"}</strong></span></div>
        <p className="production-settings-note">{text("Запрос выполняется при нажатии AI-кнопки. Предложения можно проверить перед сохранением. API-ключ хранится на сервере.", "Requests run when you click an AI action. Review proposals before saving. The API key is stored on the server.")}</p>
      </SettingsCard>
      <SettingsCard icon={<Settings2 size={18} />} title={text("Правила Restart", "Restart rules")} description={`${text("Действующие параметры для", "Current parameters for")} ${account}`}>
        <dl className="production-settings-values">
          <div><dt>{text("Цена", "Price")}</dt><dd>max(OTTO, Aftercool) − {settings.price.discountCents / 100} € → ,99</dd></div>
          <div><dt>Direct OTTO</dt><dd>OTTO standardPrice − {settings.price.discountCents / 100} € → ,99</dd></div>
          <div><dt>EAN / SKU / Reference</dt><dd>{text("Один свободный EAN — один товар", "One free EAN per product")} · {settings.ean.digits} {text("цифр", "digits")}</dd></div>
          <div><dt>{text("Остаток", "Stock quantity")}</dt><dd>{settings.fulfillment.quantity}</dd></div>
          <div><dt>{text("Обработка заказа", "Processing time")}</dt><dd>{settings.fulfillment.processingDays} {text("дней", "days")}</dd></div>
          <div><dt>{text("Профиль доставки", "Shipping profile")}</dt><dd>{settings.fulfillment.shippingProfile}</dd></div>
          <div><dt>{text("Отправка через Drip Feed", "Drip Feed submission")}</dt><dd>{enabled(settings.publication.productWriteEnabled)}</dd></div>
          <div><dt>{text("Запись остатков и доставки", "Availability writes")}</dt><dd>{enabled(settings.publication.availabilityWriteEnabled)}</dd></div>
        </dl><p className="production-settings-note">{text("Сохранение карточки сбрасывает прежнюю проверку. Запуск расписания выполняется отдельно в очереди Drip Feed.", "Editing a product invalidates its previous review. Start schedules separately in the Drip Feed queue.")}</p>
      </SettingsCard>
    </> : null}
  </section>;
}
