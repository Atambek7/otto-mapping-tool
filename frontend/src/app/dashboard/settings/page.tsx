"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { setAppLanguage } from "@/lib/i18n";
import { fetchWithAuth } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Settings,
  Save,
  Bot,
  Eye,
  EyeOff,
  User,
  ShieldCheck,
  KeyRound,
  Trash2,
  Info,
  CheckCircle2,
  AlertCircle,
  Sparkles,
  Lock,
  Globe,
  Check,
} from "lucide-react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  DialogClose,
} from "@/components/ui/dialog";

interface UserSettings {
  username: string;
  has_openai_key: boolean;
  openai_api_key_masked: string | null;
}

export default function SettingsPage() {
  const { t, i18n } = useTranslation();
  const [settings, setSettings] = useState<UserSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [showApiKey, setShowApiKey] = useState(false);
  const [inputValue, setInputValue] = useState("");
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [currentLang, setCurrentLang] = useState<"ru" | "en">("ru");

  useEffect(() => {
    loadSettings();
    const active = (i18n.language === "en" ? "en" : "ru") as "ru" | "en";
    setCurrentLang(active);
  }, [i18n.language]);

  const handleLanguageChange = (lang: "ru" | "en") => {
    setCurrentLang(lang);
    setAppLanguage(lang);
  };

  const loadSettings = async () => {
    try {
      const res = await fetchWithAuth("/settings/me");
      if (res.ok) {
        const data = await res.json();
        setSettings(data);
        setInputValue("");
      }
    } catch (e) {
      console.error("Failed to load settings", e);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    if (!inputValue.trim()) {
      toast.info(t("settings.enter_key_to_save"));
      return;
    }
    setSaving(true);
    try {
      const res = await fetchWithAuth(`/settings/me`, {
        method: "POST",
        body: JSON.stringify({
          openai_api_key: inputValue.trim(),
        }),
      });
      if (!res.ok) throw new Error("Save failed");
      const updated = await res.json();
      setSettings(updated);
      setInputValue("");
      toast.success(t("settings.saved_toast"));
    } catch (e) {
      console.error("Save error", e);
      toast.error(t("settings.save_error_toast"));
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteKey = async () => {
    setSaving(true);
    try {
      const res = await fetchWithAuth(`/settings/me`, {
        method: "POST",
        body: JSON.stringify({ openai_api_key: "" }),
      });
      if (!res.ok) throw new Error("Delete failed");
      const updated = await res.json();
      setSettings(updated);
      setInputValue("");
      setDeleteDialogOpen(false);
      toast.success(t("settings.key_deleted_toast"));
    } catch (e) {
      console.error("Delete error", e);
      toast.error(t("settings.save_error_toast"));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="max-w-3xl mx-auto space-y-6">
        <div className="space-y-2">
          <div className="h-8 w-48 bg-slate-200 dark:bg-slate-800 rounded animate-pulse" />
          <div className="h-4 w-80 bg-slate-100 dark:bg-slate-800/60 rounded animate-pulse" />
        </div>
        <div className="h-44 bg-white dark:bg-[#0B0F19] rounded-xl border border-slate-200 dark:border-slate-800 animate-pulse" />
        <div className="h-44 bg-white dark:bg-[#0B0F19] rounded-xl border border-slate-200 dark:border-slate-800 animate-pulse" />
        <div className="h-72 bg-white dark:bg-[#0B0F19] rounded-xl border border-slate-200 dark:border-slate-800 animate-pulse" />
      </div>
    );
  }

  const usernameInitials = (settings?.username || "U")
    .substring(0, 2)
    .toUpperCase();

  return (
    <div className="max-w-3xl mx-auto space-y-8 pb-16">
      {/* Page Header */}
      <div>
        <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500 mb-1">
          <span>{t("settings.preferences_access")}</span>
        </div>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-white tracking-tight">
          {t("settings.title")}
        </h1>
        <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
          {t("settings.subtitle")}
        </p>
      </div>

      {/* Language Selector Card */}
      <div className="bg-white dark:bg-[#0B0F19] border border-slate-200 dark:border-slate-800 rounded-xl shadow-sm overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-100 dark:border-slate-800/70 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-indigo-50 dark:bg-indigo-950/50 flex items-center justify-center text-indigo-600 dark:text-indigo-400">
              <Globe className="w-4 h-4" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
                {t("settings.language_title")}
              </h2>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                {t("settings.language_desc")}
              </p>
            </div>
          </div>
          <span className="text-xs font-medium px-2.5 py-1 rounded-md bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 uppercase tracking-wide">
            {currentLang === "ru" ? "RU (Русский)" : "EN (English)"}
          </span>
        </div>

        <div className="p-6">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {/* Russian Option */}
            <button
              type="button"
              onClick={() => handleLanguageChange("ru")}
              className={`text-left p-4 rounded-xl border transition-all cursor-pointer flex items-start justify-between ${
                currentLang === "ru"
                  ? "border-indigo-600 dark:border-indigo-500 bg-indigo-50/40 dark:bg-indigo-950/20 shadow-xs ring-2 ring-indigo-600/20 dark:ring-indigo-500/30"
                  : "border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700 bg-white dark:bg-slate-900/40"
              }`}
            >
              <div className="flex items-center gap-3.5">
                <span className="text-2xl select-none" role="img" aria-label="Russian">
                  🇷🇺
                </span>
                <div>
                  <div className="text-sm font-semibold text-slate-900 dark:text-white flex items-center gap-2">
                    {t("settings.lang_ru")}
                    <span className="text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400">
                      RU
                    </span>
                  </div>
                  <div className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                    {t("settings.lang_ru_sub")}
                  </div>
                </div>
              </div>
              <div
                className={`w-5 h-5 rounded-full flex items-center justify-center transition-colors shrink-0 mt-0.5 ${
                  currentLang === "ru"
                    ? "bg-indigo-600 text-white"
                    : "border border-slate-300 dark:border-slate-700"
                }`}
              >
                {currentLang === "ru" && <Check className="w-3 h-3 stroke-[3]" />}
              </div>
            </button>

            {/* English Option */}
            <button
              type="button"
              onClick={() => handleLanguageChange("en")}
              className={`text-left p-4 rounded-xl border transition-all cursor-pointer flex items-start justify-between ${
                currentLang === "en"
                  ? "border-indigo-600 dark:border-indigo-500 bg-indigo-50/40 dark:bg-indigo-950/20 shadow-xs ring-2 ring-indigo-600/20 dark:ring-indigo-500/30"
                  : "border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700 bg-white dark:bg-slate-900/40"
              }`}
            >
              <div className="flex items-center gap-3.5">
                <span className="text-2xl select-none" role="img" aria-label="English">
                  🇬🇧
                </span>
                <div>
                  <div className="text-sm font-semibold text-slate-900 dark:text-white flex items-center gap-2">
                    {t("settings.lang_en")}
                    <span className="text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400">
                      EN
                    </span>
                  </div>
                  <div className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                    {t("settings.lang_en_sub")}
                  </div>
                </div>
              </div>
              <div
                className={`w-5 h-5 rounded-full flex items-center justify-center transition-colors shrink-0 mt-0.5 ${
                  currentLang === "en"
                    ? "bg-indigo-600 text-white"
                    : "border border-slate-300 dark:border-slate-700"
                }`}
              >
                {currentLang === "en" && <Check className="w-3 h-3 stroke-[3]" />}
              </div>
            </button>
          </div>
        </div>
      </div>

      {/* Account Profile Card */}
      <div className="bg-white dark:bg-[#0B0F19] border border-slate-200 dark:border-slate-800 rounded-xl shadow-sm overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-100 dark:border-slate-800/70 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-slate-100 dark:bg-slate-800 flex items-center justify-center text-slate-600 dark:text-slate-300">
              <User className="w-4 h-4" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
                {t("settings.account_title")}
              </h2>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                {t("settings.account_desc")}
              </p>
            </div>
          </div>
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-400 border border-emerald-200/60 dark:border-emerald-800/40">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
            {t("settings.active_status")}
          </span>
        </div>

        <div className="p-6 flex items-center gap-4">
          <div className="w-14 h-14 rounded-full bg-gradient-to-br from-indigo-500 to-indigo-700 text-white font-bold text-lg flex items-center justify-center shadow-inner tracking-wider">
            {usernameInitials}
          </div>
          <div className="space-y-1">
            <div className="text-base font-semibold text-slate-900 dark:text-white flex items-center gap-2">
              {settings?.username || "Authenticated Operator"}
              <span className="text-[11px] font-normal px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 border border-slate-200 dark:border-slate-700">
                {t("settings.operator_badge")}
              </span>
            </div>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              {t("settings.account_note")}
            </p>
          </div>
        </div>
      </div>

      {/* AI Automation Integration Card */}
      <div className="bg-white dark:bg-[#0B0F19] border border-slate-200 dark:border-slate-800 rounded-xl shadow-sm overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-100 dark:border-slate-800/70 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-indigo-50 dark:bg-indigo-950/50 flex items-center justify-center text-indigo-600 dark:text-indigo-400">
              <Sparkles className="w-4 h-4" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
                {t("settings.openai_title")}
              </h2>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                {t("settings.openai_desc")}
              </p>
            </div>
          </div>

          {settings?.has_openai_key ? (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-400 border border-emerald-200/60 dark:border-emerald-800/40">
              <CheckCircle2 className="w-3.5 h-3.5" />
              {t("settings.openai_active")}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-400 border border-amber-200/60 dark:border-amber-800/40">
              <AlertCircle className="w-3.5 h-3.5" />
              {t("settings.openai_not_configured")}
            </span>
          )}
        </div>

        <div className="p-6 space-y-6">
          {/* Key Status Banner */}
          {settings?.has_openai_key ? (
            <div className="p-4 rounded-lg bg-slate-50 dark:bg-slate-800/40 border border-slate-200/80 dark:border-slate-700/60 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-md bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 flex items-center justify-center text-slate-600 dark:text-slate-300">
                  <KeyRound className="w-4 h-4 text-indigo-500" />
                </div>
                <div>
                  <div className="text-xs font-semibold text-slate-900 dark:text-white">
                    {t("settings.openai_active")}
                  </div>
                  <div className="text-xs font-mono text-slate-500 dark:text-slate-400">
                    {settings.openai_api_key_masked}
                  </div>
                </div>
              </div>

              <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
                <DialogTrigger
                  render={
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-8 text-xs text-rose-600 dark:text-rose-400 hover:text-rose-700 hover:bg-rose-50 dark:hover:bg-rose-950/30 border-rose-200 dark:border-rose-900/60"
                    >
                      <Trash2 className="w-3.5 h-3.5 mr-1.5" />
                      {t("settings.openai_delete_btn")}
                    </Button>
                  }
                />
                <DialogContent className="bg-white dark:bg-[#0B0F19] border border-slate-200 dark:border-slate-800">
                  <DialogHeader>
                    <DialogTitle className="text-base font-semibold text-slate-900 dark:text-white">
                      {t("settings.delete_dialog_title")}
                    </DialogTitle>
                    <DialogDescription className="text-sm text-slate-500 dark:text-slate-400">
                      {t("settings.delete_dialog_desc")}
                    </DialogDescription>
                  </DialogHeader>
                  <DialogFooter className="gap-2 sm:gap-0">
                    <Button
                      variant="outline"
                      onClick={() => setDeleteDialogOpen(false)}
                      className="h-9 text-xs"
                    >
                      {t("common.cancel")}
                    </Button>
                    <Button
                      onClick={handleDeleteKey}
                      className="h-9 text-xs bg-rose-600 hover:bg-rose-700 text-white ml-2"
                    >
                      {t("settings.delete_confirm")}
                    </Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
            </div>
          ) : (
            <div className="p-4 rounded-lg bg-amber-50/70 dark:bg-amber-950/20 border border-amber-200/70 dark:border-amber-900/40 flex items-start gap-3">
              <Info className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
              <div className="text-xs text-amber-800 dark:text-amber-300/90 leading-relaxed">
                {t("settings.fallback_notice")}
              </div>
            </div>
          )}

          {/* Key Input Section */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <label
                htmlFor="openai_api_key_field"
                className="text-xs font-semibold uppercase tracking-wider text-slate-700 dark:text-slate-300 flex items-center gap-1.5"
              >
                <Lock className="w-3 h-3 text-slate-400" />
                {settings?.has_openai_key ? t("settings.replace_key") : t("settings.enter_key")}
              </label>
              <span className="text-[11px] text-slate-400">
                {t("settings.never_shared")}
              </span>
            </div>

            <div className="relative">
              <Input
                id="openai_api_key_field"
                name="openai_api_key_field"
                type={showApiKey ? "text" : "password"}
                placeholder={
                  settings?.has_openai_key
                    ? t("settings.openai_placeholder_replace")
                    : t("settings.openai_placeholder")
                }
                autoComplete="new-password"
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                className="pr-10 font-mono text-xs bg-slate-50 dark:bg-slate-850 border-slate-200 dark:border-slate-700 h-10 focus-visible:ring-indigo-500"
              />
              <button
                type="button"
                onClick={() => setShowApiKey(!showApiKey)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 transition-colors"
                aria-label={showApiKey ? "Hide key" : "Show key"}
              >
                {showApiKey ? (
                  <EyeOff className="w-4 h-4" />
                ) : (
                  <Eye className="w-4 h-4" />
                )}
              </button>
            </div>

            <div className="rounded-lg p-3 bg-slate-50 dark:bg-slate-800/30 border border-slate-100 dark:border-slate-800 text-[11px] text-slate-500 dark:text-slate-400 space-y-1">
              <div className="flex items-center gap-1.5 font-medium text-slate-700 dark:text-slate-300">
                <ShieldCheck className="w-3.5 h-3.5 text-indigo-500" />
                {t("settings.sec_title")}
              </div>
              <p>
                {t("settings.sec_body")}
              </p>
            </div>
          </div>
        </div>

        {/* Footer Actions */}
        <div className="px-6 py-3.5 bg-slate-50/80 dark:bg-slate-800/40 border-t border-slate-100 dark:border-slate-800 flex items-center justify-between">
          <span className="text-xs text-slate-400">
            {inputValue.trim()
              ? t("settings.unsaved_buffer")
              : t("settings.all_up_to_date")}
          </span>
          <Button
            onClick={handleSave}
            disabled={saving || !inputValue.trim()}
            className="h-9 px-5 bg-indigo-600 hover:bg-indigo-700 text-white font-medium text-xs shadow-sm transition-colors"
          >
            <Save className="w-3.5 h-3.5 mr-1.5" />
            {saving ? t("common.saving") : t("settings.openai_save_btn")}
          </Button>
        </div>
      </div>
    </div>
  );
}

