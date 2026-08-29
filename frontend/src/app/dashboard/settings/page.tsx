"use client";

import { useEffect, useState } from "react";
import { fetchWithAuth } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Settings, Save, Bot, Eye, EyeOff, User } from "lucide-react";

interface UserSettings {
  username: string;
  openai_api_key: string | null;
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<UserSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [showApiKey, setShowApiKey] = useState(false);
  const [inputValue, setInputValue] = useState("");

  useEffect(() => {
    loadSettings();
  }, []);

  const loadSettings = async () => {
    try {
      const res = await fetchWithAuth("/settings/me");
      if (res.ok) {
        const data = await res.json();
        setSettings(data);
        setInputValue(data.openai_api_key || "");
      }
    } catch (e) {
      console.error("Failed to load settings", e);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const res = await fetchWithAuth(`/settings/me`, {
        method: "POST",
        body: JSON.stringify({
          openai_api_key: inputValue.trim() || null,
        }),
      });
      if (!res.ok) throw new Error("Save failed");
      alert("Настройки успешно сохранены!");
    } catch (e) {
      console.error("Save error", e);
      alert("Не удалось сохранить настройки");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="p-8 text-slate-500">Загрузка настроек...</div>;
  }

  return (
    <div className="max-w-2xl mx-auto space-y-8 pb-16">
      
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-white tracking-tight flex items-center gap-3">
          <Settings className="w-6 h-6 text-indigo-500" /> 
          Мои настройки
        </h1>
        <p className="text-slate-500 mt-2 text-sm">
          Управляйте вашими персональными настройками. Эти ключи доступны только вам.
        </p>
      </div>

      <div className="bg-white dark:bg-[#0B0F19] border border-slate-200 dark:border-slate-800 rounded-2xl shadow-sm overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-100 dark:border-slate-800/60 bg-slate-50/50 dark:bg-slate-800/30">
          <h2 className="text-lg font-semibold text-slate-900 dark:text-white flex items-center gap-2">
            <User className="w-5 h-5 text-indigo-500" />
            Учетная запись: <span className="text-indigo-600 dark:text-indigo-400">{settings?.username}</span>
          </h2>
        </div>
        
        <div className="p-6 space-y-6">
          
          {/* OpenAI API Key */}
          <div className="space-y-4">
            <h3 className="text-sm font-medium text-slate-900 dark:text-slate-300 flex items-center gap-2">
              <Bot className="w-4 h-4 text-indigo-500" />
              Персональный OpenAI API Key
            </h3>
            
            <div className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1.5">OPENAI_API_KEY</label>
                <div className="relative">
                  <Input 
                    name="openai_api_key_field"
                    type={showApiKey ? "text" : "password"}
                    placeholder="sk-..." 
                    autoComplete="new-password"
                    value={inputValue}
                    onChange={(e) => setInputValue(e.target.value)}
                    className="bg-slate-50 dark:bg-slate-800/50 pr-10"
                  />
                  <button 
                    type="button"
                    onClick={() => setShowApiKey(!showApiKey)}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
                  >
                    {showApiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
                <p className="text-[11px] text-slate-400 mt-2 flex flex-col gap-1">
                  <span>Этот ключ привязан лично к вашему логину ({settings?.username}). Другие пользователи его не увидят.</span>
                  <span>Если поле пустое, будет использоваться общий ключ с сервера (из .env).</span>
                </p>
              </div>
            </div>
          </div>

        </div>

        <div className="px-6 py-4 bg-slate-50/80 dark:bg-slate-800/20 border-t border-slate-100 dark:border-slate-800 flex justify-end">
          <Button 
            onClick={handleSave} 
            disabled={saving}
            className="bg-indigo-600 hover:bg-indigo-700 text-white gap-2"
          >
            <Save className="w-4 h-4" />
            {saving ? "Сохранение..." : "Сохранить"}
          </Button>
        </div>
      </div>
    </div>
  );
}
