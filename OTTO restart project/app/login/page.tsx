"use client";

import { Suspense, useState } from "react";
import Image from "next/image";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertCircle, Eye, EyeOff, Loader2, Lock, LogIn, User } from "lucide-react";
import { useTranslation } from "@/features/i18n";
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
  const { t } = useTranslation();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
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
        setError(data.message || t("auth.invalidCredentials"));
        setLoading(false);
        return;
      }

      // Successful login
      router.replace(safeNext);
      router.refresh();
    } catch (err) {
      setError(t("common.error"));
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
          <h1 className="login-title">{t("auth.title")}</h1>
          <p className="login-subtitle">
            {t("auth.subtitle")}
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
              {t("auth.usernameLabel")}
            </label>
            <div className="login-input-wrapper">
              <User size={16} className="login-input-icon" />
              <input
                id="username"
                type="text"
                className="login-input"
                placeholder={t("auth.usernamePlaceholder")}
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
              {t("auth.passwordLabel")}
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
                title={showPassword ? "✕" : "👁"}
                aria-label={showPassword ? "✕" : "👁"}
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
                <span>{t("auth.loggingIn")}</span>
              </>
            ) : (
              <>
                <LogIn size={16} />
                <span>{t("auth.loginBtn")}</span>
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
