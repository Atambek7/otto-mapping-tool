"use client";

import i18n from "i18next";
import { initReactI18next, useTranslation } from "react-i18next";
import { ru } from "./locales/ru";
import { en } from "./locales/en";

export const STORAGE_LANG_KEY = "otto_restart_lang";

export const resources = {
  ru: { translation: ru },
  en: { translation: en },
} as const;

export type SupportedLanguage = "ru" | "en";

export function getInitialLanguage(): SupportedLanguage {
  if (typeof window !== "undefined") {
    try {
      const saved = localStorage.getItem(STORAGE_LANG_KEY);
      if (saved === "ru" || saved === "en") return saved;
    } catch {
      // ignore
    }
  }
  return "ru";
}

if (!i18n.isInitialized) {
  i18n
    .use(initReactI18next)
    .init({
      resources,
      lng: getInitialLanguage(),
      fallbackLng: "ru",
      interpolation: {
        escapeValue: false,
      },
      react: {
        useSuspense: false,
      },
    });
}

export function setAppLanguage(lang: SupportedLanguage) {
  i18n.changeLanguage(lang);
  if (typeof window !== "undefined") {
    try {
      localStorage.setItem(STORAGE_LANG_KEY, lang);
      window.dispatchEvent(new CustomEvent("otto_restart_lang_changed", { detail: lang }));
    } catch {
      // ignore
    }
  }
}

export function getAppLanguage(): SupportedLanguage {
  const current = i18n.language;
  return current === "en" ? "en" : "ru";
}

export { useTranslation };
export default i18n;
