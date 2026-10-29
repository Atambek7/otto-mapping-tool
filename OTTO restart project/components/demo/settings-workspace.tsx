"use client";

import { useState, useEffect } from "react";
import {
  Globe,
  User,
  Sparkles,
  CheckCircle2,
  AlertCircle,
  KeyRound,
  RefreshCw,
  Trash2,
  Lock,
  Eye,
  EyeOff,
  ShieldCheck,
  Check,
  Info,
  Save,
  CheckCircle,
} from "lucide-react";
import { useRestartAccount } from "@/features/state/restart-account";
import "./settings.css";

type LanguageType = "ru" | "en";

interface CheckKeyResult {
  status: "active" | "exhausted";
  email: string;
  organization: string;
  hasSpendLimit: boolean;
}

const TRANSLATIONS = {
  ru: {
    eyebrow: "ПАРАМЕТРЫ И ДОСТУП",
    title: "Настройки системы",
    subtitle: "Управление языком интерфейса, ключами нейросети и параметрами аккаунта",
    langCardTitle: "Язык интерфейса",
    langCardDesc: "Выберите предпочтительный язык для отображения интерфейса",
    langRuTitle: "Русский",
    langRuDesc: "Русский язык интерфейса",
    langEnTitle: "English",
    langEnDesc: "English interface language",
    profileTitle: "Профиль пользователя",
    profileDesc: "Данные текущей учетной записи в системе",
    activeStatus: "Активен",
    operatorBadge: "Оператор",
    operatorName: "Авторизованный оператор",
    accountPrefix: "Аккаунт",
    profileNote:
      "Авторизованный доступ к механизму перезапуска каталога OttoRestart 3/12M и планировщику Drip Feed.",
    openaiTitle: "ChatGPT / OpenAI API Ключ",
    openaiDesc:
      "Используется для ИИ-оптимизации названий (≤ 70 симв.), нейромаппинга категорий и извлечения обязательных атрибутов товаров через GPT-4o",
    keyActive: "API ключ активен",
    keyNotConfigured: "API ключ не настроен",
    maskedKeyDefault: "sk-proj-49a8B••••••••••••••••••••••••9F2c",
    checkKeyBtn: "Проверить ключ и баланс",
    checkingKey: "Проверка связи с OpenAI...",
    deleteKeyBtn: "Удалить ключ",
    fallbackNotice:
      "Персональный ключ не настроен. Система использует базовый ключ сервера при его наличии.",
    replaceKey: "Заменить API ключ",
    enterKey: "Введите OpenAI API ключ",
    neverShared: "Никогда не передается третьим лицам",
    placeholderReplace: "Введите новый ключ для замены (sk-...)",
    placeholder: "Введите sk-proj-...",
    secTitle: "Безопасность и изоляция данных",
    secBody:
      "Ваш API ключ шифруется и строго привязан к вашей учетной записи. Он никогда не отображается целиком.",
    diagActive: "Соединение с OpenAI успешно и баланс активен!",
    diagExhausted: "Баланс OpenAI исчерпан (credit_balance_exhausted)",
    diagEmail: "Email",
    diagOrg: "Организация",
    diagSpendLimit: "Spend Limit проекта",
    diagLimitUnlimited: "Не ограничен",
    unsavedBuffer: "Несохраненный ключ в поле ввода",
    allUpToDate: "Все параметры сохранены",
    saveBtn: "Сохранить настройки",
    saving: "Сохраняем...",
    deleteModalTitle: "Удалить ключ OpenAI?",
    deleteModalDesc:
      "После удаления автоматический нейромаппинг товаров и ИИ-оптимизация названий станут недоступны до ввода нового ключа.",
    cancel: "Отмена",
    deleteConfirm: "Да, удалить",
    toastSaved: "Настройки успешно сохранены!",
    toastDeleted: "API ключ успешно удален",
    toastChecked: "Связь с OpenAI проверена: ключ валиден и баланс активен",
  },
  en: {
    eyebrow: "PARAMETERS & ACCESS",
    title: "System Settings",
    subtitle: "Manage interface language, neural network keys, and account parameters",
    langCardTitle: "Interface Language",
    langCardDesc: "Choose your preferred language for the interface display",
    langRuTitle: "Русский",
    langRuDesc: "Russian interface language",
    langEnTitle: "English",
    langEnDesc: "English interface language",
    profileTitle: "User Profile",
    profileDesc: "Current system account details",
    activeStatus: "Active",
    operatorBadge: "Operator",
    operatorName: "Authenticated Operator",
    accountPrefix: "Account",
    profileNote:
      "Authorized access to OttoRestart 3/12M catalog engine and Drip Feed scheduler.",
    openaiTitle: "ChatGPT / OpenAI API Key",
    openaiDesc:
      "Used for AI title optimization (≤ 70 chars), category neural mapping, and extracting required product attributes via GPT-4o",
    keyActive: "API Key Active",
    keyNotConfigured: "API Key Not Configured",
    maskedKeyDefault: "sk-proj-49a8B••••••••••••••••••••••••9F2c",
    checkKeyBtn: "Check key & balance",
    checkingKey: "Checking OpenAI connection...",
    deleteKeyBtn: "Delete key",
    fallbackNotice:
      "Personal key is not configured. The system will use the server fallback key if available.",
    replaceKey: "Replace API key",
    enterKey: "Enter OpenAI API key",
    neverShared: "Never shared with third parties",
    placeholderReplace: "Enter new key to replace (sk-...)",
    placeholder: "Enter sk-proj-...",
    secTitle: "Security & Data Isolation",
    secBody:
      "Your API key is encrypted and strictly bound to your account. It is never displayed in full.",
    diagActive: "Connection to OpenAI successful and balance is active!",
    diagExhausted: "OpenAI balance exhausted (credit_balance_exhausted)",
    diagEmail: "Email",
    diagOrg: "Organization",
    diagSpendLimit: "Project Spend Limit",
    diagLimitUnlimited: "Unlimited",
    unsavedBuffer: "Unsaved key in input buffer",
    allUpToDate: "All parameters saved",
    saveBtn: "Save Settings",
    saving: "Saving...",
    deleteModalTitle: "Delete OpenAI Key?",
    deleteModalDesc:
      "After deletion, automated category mapping and AI title optimization will be unavailable until a new key is provided.",
    cancel: "Cancel",
    deleteConfirm: "Yes, delete",
    toastSaved: "Settings saved successfully!",
    toastDeleted: "API key successfully deleted",
    toastChecked: "OpenAI connection verified: key is valid and balance is active",
  },
};

export function DemoSettingsWorkspace() {
  const { account } = useRestartAccount();

  // Language state
  const [lang, setLang] = useState<LanguageType>("ru");

  // OpenAI Key states
  const [hasKey, setHasKey] = useState(true);
  const [maskedKey, setMaskedKey] = useState("sk-proj-49a8B••••••••••••••••••••••••9F2c");
  const [inputValue, setInputValue] = useState("");
  const [showKeyText, setShowKeyText] = useState(false);
  const [checkingKey, setCheckingKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  const [diagnosticResult, setDiagnosticResult] = useState<CheckKeyResult | null>(null);

  // Toast feedback
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  // Load persisted language & key from localStorage
  useEffect(() => {
    try {
      const savedLang = localStorage.getItem("otto_restart_lang");
      if (savedLang === "en" || savedLang === "ru") {
        setLang(savedLang);
      }
      const savedHasKey = localStorage.getItem("otto_restart_has_key");
      if (savedHasKey !== null) {
        setHasKey(savedHasKey === "true");
      }
      const savedMaskedKey = localStorage.getItem("otto_restart_masked_key");
      if (savedMaskedKey) {
        setMaskedKey(savedMaskedKey);
      }
    } catch {
      // localStorage not accessible
    }
  }, []);

  const t = TRANSLATIONS[lang];

  function showToast(msg: string) {
    setToastMessage(msg);
    setTimeout(() => setToastMessage(null), 3500);
  }

  function handleLanguageChange(newLang: LanguageType) {
    setLang(newLang);
    try {
      localStorage.setItem("otto_restart_lang", newLang);
    } catch {
      // ignore
    }
  }

  function handleCheckKey() {
    setCheckingKey(true);
    setDiagnosticResult(null);

    setTimeout(() => {
      setCheckingKey(false);
      setDiagnosticResult({
        status: "active",
        email: account === "JV" ? "operator@varvara.de" : "account@xlmoebel.de",
        organization: account === "JV" ? "JV MÖBEL (CN)" : "XLMOEBEL",
        hasSpendLimit: false,
      });
      showToast(t.toastChecked);
    }, 900);
  }

  function handleSaveKey() {
    if (!inputValue.trim()) return;

    setSaving(true);
    setTimeout(() => {
      const clean = inputValue.trim();
      const prefix = clean.length > 8 ? clean.slice(0, 8) : "sk-proj-";
      const suffix = clean.length > 4 ? clean.slice(-4) : "xxxx";
      const newMasked = `${prefix}••••••••••••••••••••••••${suffix}`;

      setHasKey(true);
      setMaskedKey(newMasked);
      setInputValue("");
      setSaving(false);

      try {
        localStorage.setItem("otto_restart_has_key", "true");
        localStorage.setItem("otto_restart_masked_key", newMasked);
      } catch {
        // ignore
      }

      showToast(t.toastSaved);
    }, 400);
  }

  function handleDeleteKey() {
    setHasKey(false);
    setMaskedKey("");
    setInputValue("");
    setDiagnosticResult(null);
    setDeleteModalOpen(false);

    try {
      localStorage.setItem("otto_restart_has_key", "false");
      localStorage.removeItem("otto_restart_masked_key");
    } catch {
      // ignore
    }

    showToast(t.toastDeleted);
  }

  return (
    <div className="otto-settings-container">
      {/* Page Header */}
      <div className="otto-settings-header">
        <div className="otto-settings-eyebrow">
          <span>{t.eyebrow}</span>
        </div>
        <h1 className="otto-settings-title">{t.title}</h1>
        <p className="otto-settings-subtitle">{t.subtitle}</p>
      </div>

      {/* Card 1: Language Selector Card */}
      <section className="otto-settings-card" aria-labelledby="lang-card-title">
        <div className="otto-card-header">
          <div className="otto-card-header-left">
            <div className="otto-card-icon-box icon-accent">
              <Globe size={18} />
            </div>
            <div className="otto-card-title-wrap">
              <h2 id="lang-card-title">{t.langCardTitle}</h2>
              <p>{t.langCardDesc}</p>
            </div>
          </div>
          <span className="otto-status-badge badge-neutral">
            {lang === "ru" ? "RU (РУССКИЙ)" : "EN (ENGLISH)"}
          </span>
        </div>

        <div className="otto-card-body">
          <div className="otto-lang-grid">
            {/* Russian Option */}
            <button
              type="button"
              className={`otto-lang-option ${lang === "ru" ? "lang-selected" : ""}`}
              onClick={() => handleLanguageChange("ru")}
            >
              <div className="otto-lang-content">
                <span className="otto-lang-flag" role="img" aria-label="Russian">
                  🇷🇺
                </span>
                <div className="otto-lang-label-wrap">
                  <div className="otto-lang-name">
                    <span>{t.langRuTitle}</span>
                    <span className="otto-lang-pill">RU</span>
                  </div>
                  <div className="otto-lang-subtext">{t.langRuDesc}</div>
                </div>
              </div>
              <div className="otto-lang-radio">
                {lang === "ru" && <Check size={12} strokeWidth={3} />}
              </div>
            </button>

            {/* English Option */}
            <button
              type="button"
              className={`otto-lang-option ${lang === "en" ? "lang-selected" : ""}`}
              onClick={() => handleLanguageChange("en")}
            >
              <div className="otto-lang-content">
                <span className="otto-lang-flag" role="img" aria-label="English">
                  🇬🇧
                </span>
                <div className="otto-lang-label-wrap">
                  <div className="otto-lang-name">
                    <span>{t.langEnTitle}</span>
                    <span className="otto-lang-pill">EN</span>
                  </div>
                  <div className="otto-lang-subtext">{t.langEnDesc}</div>
                </div>
              </div>
              <div className="otto-lang-radio">
                {lang === "en" && <Check size={12} strokeWidth={3} />}
              </div>
            </button>
          </div>
        </div>
      </section>

      {/* Card 2: User Profile Card */}
      <section className="otto-settings-card" aria-labelledby="profile-card-title">
        <div className="otto-card-header">
          <div className="otto-card-header-left">
            <div className="otto-card-icon-box icon-neutral">
              <User size={18} />
            </div>
            <div className="otto-card-title-wrap">
              <h2 id="profile-card-title">{t.profileTitle}</h2>
              <p>{t.profileDesc}</p>
            </div>
          </div>
          <span className="otto-status-badge badge-active">
            <span className="otto-pulse-dot" />
            <span>{t.activeStatus}</span>
          </span>
        </div>

        <div className="otto-card-body">
          <div className="otto-profile-row">
            <div className="otto-profile-avatar">{account}</div>
            <div className="otto-profile-info">
              <div className="otto-profile-name-row">
                <span className="otto-profile-name">{t.operatorName}</span>
                <span className="otto-profile-role-badge">{t.operatorBadge}</span>
                <span className="otto-profile-account-badge">
                  {t.accountPrefix}: {account}
                </span>
              </div>
              <p className="otto-profile-desc">{t.profileNote}</p>
            </div>
          </div>
        </div>
      </section>

      {/* Card 3: ChatGPT / OpenAI API Key Card */}
      <section className="otto-settings-card" aria-labelledby="openai-card-title">
        <div className="otto-card-header">
          <div className="otto-card-header-left">
            <div className="otto-card-icon-box icon-accent">
              <Sparkles size={18} />
            </div>
            <div className="otto-card-title-wrap">
              <h2 id="openai-card-title">{t.openaiTitle}</h2>
              <p>{t.openaiDesc}</p>
            </div>
          </div>

          {hasKey ? (
            <span className="otto-status-badge badge-active">
              <CheckCircle2 size={13} />
              <span>{t.keyActive}</span>
            </span>
          ) : (
            <span className="otto-status-badge badge-warning">
              <AlertCircle size={13} />
              <span>{t.keyNotConfigured}</span>
            </span>
          )}
        </div>

        <div className="otto-card-body">
          {/* Key Status Banner */}
          {hasKey ? (
            <div className="otto-key-active-box">
              <div className="otto-key-active-left">
                <div className="otto-key-icon-wrap">
                  <KeyRound size={16} />
                </div>
                <div className="otto-key-details">
                  <span className="otto-key-details-title">{t.keyActive}</span>
                  <span className="otto-key-details-masked">{maskedKey}</span>
                </div>
              </div>

              <div className="otto-key-actions">
                <button
                  type="button"
                  className="otto-btn-secondary"
                  onClick={handleCheckKey}
                  disabled={checkingKey}
                  title={t.checkKeyBtn}
                >
                  <RefreshCw size={13} className={checkingKey ? "animate-spin" : ""} />
                  <span>{checkingKey ? t.checkingKey : t.checkKeyBtn}</span>
                </button>

                <button
                  type="button"
                  className="otto-btn-danger"
                  onClick={() => setDeleteModalOpen(true)}
                  title={t.deleteKeyBtn}
                >
                  <Trash2 size={13} />
                  <span>{t.deleteKeyBtn}</span>
                </button>
              </div>
            </div>
          ) : (
            <div className="otto-notice-banner">
              <Info size={18} className="shrink-0" />
              <div className="otto-notice-banner-text">{t.fallbackNotice}</div>
            </div>
          )}

          {/* Key Input Section */}
          <div className="otto-input-section">
            <div className="otto-input-label-row">
              <label htmlFor="openai-key-input" className="otto-input-label">
                <Lock size={12} />
                <span>{hasKey ? t.replaceKey : t.enterKey}</span>
              </label>
              <span className="otto-input-subnote">{t.neverShared}</span>
            </div>

            <div className="otto-key-input-wrap">
              <input
                id="openai-key-input"
                type={showKeyText ? "text" : "password"}
                className="otto-key-input"
                placeholder={hasKey ? t.placeholderReplace : t.placeholder}
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                autoComplete="off"
                spellCheck={false}
              />
              <button
                type="button"
                className="otto-eye-btn"
                onClick={() => setShowKeyText((prev) => !prev)}
                title={showKeyText ? "Hide key" : "Show key"}
                aria-label={showKeyText ? "Hide key" : "Show key"}
              >
                {showKeyText ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </div>

            {/* Security Note */}
            <div className="otto-security-box">
              <div className="otto-security-title">
                <ShieldCheck size={14} />
                <span>{t.secTitle}</span>
              </div>
              <div>{t.secBody}</div>
            </div>

            {/* Diagnostic Result Banner */}
            {diagnosticResult && (
              <div
                className={`otto-diagnostic-box ${
                  diagnosticResult.status === "exhausted" ? "diag-exhausted" : ""
                }`}
              >
                {diagnosticResult.status === "active" ? (
                  <CheckCircle2 size={18} className="text-emerald-400 shrink-0" />
                ) : (
                  <AlertCircle size={18} className="text-rose-400 shrink-0" />
                )}
                <div className="otto-diagnostic-content">
                  <div className="otto-diagnostic-title">
                    {diagnosticResult.status === "active" ? t.diagActive : t.diagExhausted}
                  </div>

                  <div className="otto-diagnostic-grid">
                    <div>
                      <span className="diag-muted">{t.diagEmail}: </span>
                      <strong>{diagnosticResult.email}</strong>
                    </div>
                    <div>
                      <span className="diag-muted">{t.diagOrg}: </span>
                      <strong>{diagnosticResult.organization}</strong>
                    </div>
                    <div>
                      <span className="diag-muted">{t.diagSpendLimit}: </span>
                      <strong>{t.diagLimitUnlimited}</strong>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Card Footer */}
        <div className="otto-card-footer">
          <span className="otto-footer-status">
            {inputValue.trim() ? t.unsavedBuffer : t.allUpToDate}
          </span>

          <div className="otto-footer-actions">
            {(hasKey || inputValue.trim()) && (
              <button
                type="button"
                className="otto-btn-secondary"
                onClick={handleCheckKey}
                disabled={checkingKey || saving}
              >
                <RefreshCw size={13} className={checkingKey ? "animate-spin" : ""} />
                <span>{checkingKey ? t.checkingKey : t.checkKeyBtn}</span>
              </button>
            )}

            <button
              type="button"
              className="otto-btn-primary"
              onClick={handleSaveKey}
              disabled={saving || !inputValue.trim()}
            >
              <Save size={14} />
              <span>{saving ? t.saving : t.saveBtn}</span>
            </button>
          </div>
        </div>
      </section>

      {/* Delete Confirmation Modal Dialog */}
      {deleteModalOpen && (
        <div
          className="otto-modal-backdrop"
          onClick={() => setDeleteModalOpen(false)}
          role="dialog"
          aria-modal="true"
        >
          <div className="otto-modal-dialog" onClick={(e) => e.stopPropagation()}>
            <h3 className="otto-modal-title">{t.deleteModalTitle}</h3>
            <p className="otto-modal-desc">{t.deleteModalDesc}</p>
            <div className="otto-modal-actions">
              <button
                type="button"
                className="otto-btn-secondary"
                onClick={() => setDeleteModalOpen(false)}
              >
                {t.cancel}
              </button>
              <button
                type="button"
                className="otto-btn-danger"
                onClick={handleDeleteKey}
              >
                <Trash2 size={13} />
                <span>{t.deleteConfirm}</span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Toast Notification */}
      {toastMessage && (
        <div className="otto-settings-toast" role="status">
          <CheckCircle size={16} />
          <span>{toastMessage}</span>
        </div>
      )}
    </div>
  );
}
