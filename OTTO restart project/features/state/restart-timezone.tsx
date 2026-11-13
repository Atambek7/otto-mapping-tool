"use client";

import { createContext, useContext, useEffect, useState, useMemo } from "react";
import type { ReactNode } from "react";

export interface TimezoneOption {
  id: string;
  name: string;
  offsetName: string;
  subtext: string;
  badge: string;
}

export const TIMEZONE_OPTIONS: TimezoneOption[] = [
  {
    id: "auto",
    name: "Авто (Браузер)",
    offsetName: "Местное время",
    subtext: "Определяется автоматически по настройкам браузера",
    badge: "AUTO",
  },
  {
    id: "Europe/Berlin",
    name: "Берлин (OTTO Market)",
    offsetName: "CET · UTC+1",
    subtext: "Германия · Официальное время маркетплейса OTTO",
    badge: "CET",
  },
  {
    id: "Europe/Moscow",
    name: "Москва",
    offsetName: "MSK · UTC+3",
    subtext: "Московское стандартное время",
    badge: "MSK",
  },
  {
    id: "Asia/Tashkent",
    name: "Ташкент / Самарканд",
    offsetName: "UZT · UTC+5",
    subtext: "Стандартное время Узбекистана",
    badge: "UZT",
  },
  {
    id: "Asia/Almaty",
    name: "Алматы / Астана",
    offsetName: "ALMT · UTC+5",
    subtext: "Стандартное время Казахстана",
    badge: "ALMT",
  },
  {
    id: "Asia/Dubai",
    name: "Дубай",
    offsetName: "GST · UTC+4",
    subtext: "Стандартное время ОАЭ (Персидский залив)",
    badge: "GST",
  },
  {
    id: "UTC",
    name: "UTC (Серверное время)",
    offsetName: "UTC+0",
    subtext: "Универсальное координированное время",
    badge: "UTC",
  },
];

export interface FormattedEventTime {
  date: string;
  time: string;
  full: string;
  tzBadge: string;
}

interface TimezoneContextType {
  timezone: string;
  resolvedTimezone: string;
  detectedBrowserTz: string;
  setTimezone: (tz: string) => void;
  formatDate: (value: string | null | undefined) => string;
  formatEventTime: (value: string | null | undefined) => FormattedEventTime;
}

const TimezoneContext = createContext<TimezoneContextType | null>(null);

const STORAGE_KEY = "otto_restart_timezone";

export function RestartTimezoneProvider({ children }: { children: ReactNode }) {
  const [timezone, setTimezoneState] = useState<string>("auto");
  const [detectedBrowserTz, setDetectedBrowserTz] = useState<string>("UTC");

  useEffect(() => {
    try {
      const browserTz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
      setDetectedBrowserTz(browserTz);

      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) {
        setTimezoneState(saved);
      }
    } catch {
      // In-memory selection fallback
    }
  }, []);

  const resolvedTimezone = useMemo(() => {
    if (timezone === "auto") {
      return detectedBrowserTz;
    }
    return timezone;
  }, [timezone, detectedBrowserTz]);

  const tzBadge = useMemo(() => {
    const match = TIMEZONE_OPTIONS.find((opt) => opt.id === timezone);
    if (match && match.id !== "auto") return match.badge;
    if (timezone === "auto") {
      if (detectedBrowserTz === "Asia/Tashkent" || detectedBrowserTz === "Asia/Samarkand") return "UZT";
      if (detectedBrowserTz === "Europe/Moscow") return "MSK";
      if (detectedBrowserTz === "Europe/Berlin") return "CET";
      if (detectedBrowserTz === "UTC") return "UTC";
      return "LOCAL";
    }
    return timezone;
  }, [timezone, detectedBrowserTz]);

  function setTimezone(tz: string) {
    setTimezoneState(tz);
    try {
      localStorage.setItem(STORAGE_KEY, tz);
    } catch {
      // Ignore localStorage errors
    }
  }

  function formatDate(value: string | null | undefined): string {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    try {
      return new Intl.DateTimeFormat("ru-RU", {
        dateStyle: "short",
        timeStyle: "short",
        timeZone: resolvedTimezone,
      }).format(date);
    } catch {
      return date.toISOString().slice(0, 16).replace("T", " ");
    }
  }

  function formatEventTime(value: string | null | undefined): FormattedEventTime {
    if (!value) return { date: "—", time: "—", full: "—", tzBadge: "—" };
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return { date: "—", time: "—", full: "—", tzBadge: "—" };

    try {
      const date = new Intl.DateTimeFormat("ru-RU", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        timeZone: resolvedTimezone,
      }).format(d);

      const time = new Intl.DateTimeFormat("ru-RU", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        timeZone: resolvedTimezone,
      }).format(d);

      return {
        date,
        time,
        full: `${date}, ${time} (${tzBadge})`,
        tzBadge,
      };
    } catch {
      return {
        date: d.toISOString().slice(0, 10),
        time: d.toISOString().slice(11, 19),
        full: `${d.toISOString()} (UTC)`,
        tzBadge: "UTC",
      };
    }
  }

  return (
    <TimezoneContext.Provider
      value={{
        timezone,
        resolvedTimezone,
        detectedBrowserTz,
        setTimezone,
        formatDate,
        formatEventTime,
      }}
    >
      {children}
    </TimezoneContext.Provider>
  );
}

export function useRestartTimezone() {
  const context = useContext(TimezoneContext);
  if (!context) {
    return {
      timezone: "auto",
      resolvedTimezone: "UTC",
      detectedBrowserTz: "UTC",
      setTimezone: () => {},
      formatDate: (val: string | null | undefined) => (val ? new Date(val).toLocaleString("ru-RU") : "—"),
      formatEventTime: (val: string | null | undefined) => ({
        date: val ? new Date(val).toLocaleDateString("ru-RU") : "—",
        time: val ? new Date(val).toLocaleTimeString("ru-RU") : "—",
        full: val ? new Date(val).toLocaleString("ru-RU") : "—",
        tzBadge: "UTC",
      }),
    };
  }
  return context;
}
