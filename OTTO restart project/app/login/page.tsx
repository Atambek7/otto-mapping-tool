"use client";

import { Suspense, useState } from "react";
import Image from "next/image";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertCircle, Eye, EyeOff, Loader2, Lock, LogIn, User } from "lucide-react";
import logoRed from "@/components/demo/images/otto-logo_red.png";
import logoBrown from "@/components/demo/images/otto_log_brown.png";
import "./login.css";

export default function LoginPage() {
  return (
    <Suspense fallback={<div className="login-viewport" />}>
      <LoginFormContent />
    </Suspense>
  );
}

function LoginFormContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("admin");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const rawNext = searchParams.get("next") || "/";
  // Safe redirect sanitization (prevent open redirects)
  const safeNext =
    rawNext.startsWith("/") && !rawNext.startsWith("//") ? rawNext : "/";

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (loading) return;

    setError(null);
    setLoading(true);

    try {
      const res = await fetch("/restart/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });

      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        setError(data.message || "Неверный логин или пароль");
        setLoading(false);
        return;
      }

      // Successful login
      router.replace(safeNext);
      router.refresh();
    } catch (err) {
      setError("Ошибка соединения с сервером");
      setLoading(false);
    }
  }

  return (
    <div className="login-viewport">
      <div className="login-card">
        <div className="login-brand-header">
          <div className="login-logo-container product-logo-wrap">
            <Image
              src={logoRed}
              alt="OTTO Restart"
              className="brand-logo-img logo-dark"
              width={124}
              height={34}
              priority
            />
            <Image
              src={logoBrown}
              alt="OTTO Restart"
              className="brand-logo-img logo-light"
              width={124}
              height={34}
              priority
            />
          </div>
          <h1 className="login-title">Вход в систему</h1>
          <p className="login-subtitle">
            Управление Restart 3/12M и OTTO Library
          </p>
        </div>

        {error && (
          <div className="login-error-banner" role="alert">
            <AlertCircle size={16} />
            <span>{error}</span>
          </div>
        )}

        <form className="login-form" onSubmit={handleSubmit}>
          <div className="login-field">
            <label className="login-label" htmlFor="username">
              Имя пользователя
            </label>
            <div className="login-input-wrapper">
              <User size={16} className="login-input-icon" />
              <input
                id="username"
                type="text"
                className="login-input"
                placeholder="admin"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                required
                disabled={loading}
              />
            </div>
          </div>

          <div className="login-field">
            <label className="login-label" htmlFor="password">
              Пароль
            </label>
            <div className="login-input-wrapper">
              <Lock size={16} className="login-input-icon" />
              <input
                id="password"
                type={showPassword ? "text" : "password"}
                className="login-input"
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
                disabled={loading}
              />
              <button
                type="button"
                className="login-password-toggle"
                onClick={() => setShowPassword((prev) => !prev)}
                title={showPassword ? "Скрыть пароль" : "Показать пароль"}
                aria-label={showPassword ? "Скрыть пароль" : "Показать пароль"}
              >
                {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </div>

          <button
            type="submit"
            className="login-submit-btn"
            disabled={loading || !username.trim() || !password}
          >
            {loading ? (
              <>
                <Loader2 size={16} className="spin" />
                <span>Проверка доступа…</span>
              </>
            ) : (
              <>
                <LogIn size={16} />
                <span>Войти</span>
              </>
            )}
          </button>
        </form>

        <p className="login-footer-hint">
          Стандартный доступ: <code>admin</code> / <code>admin</code>
        </p>
      </div>
    </div>
  );
}

