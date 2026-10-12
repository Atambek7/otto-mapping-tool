import { toast } from "sonner";

export interface OpenAiErrorInfo {
  title: string;
  description: string;
  actionLabel?: string;
  actionUrl?: string;
}

export function parseOpenAiError(
  rawError: string | undefined | null,
  t: (key: string, opts?: any) => string
): OpenAiErrorInfo {
  const text = String(rawError || "").trim();
  const lower = text.toLowerCase();

  // 1. Quota / Credits exhausted
  if (
    lower.includes("credit_balance_exhausted") ||
    lower.includes("insufficient_quota") ||
    lower.includes("no credits remaining") ||
    lower.includes("quota exceeded") ||
    lower.includes("закончились средства") ||
    lower.includes("insufficient quota")
  ) {
    return {
      title: t("product_drawer.toast_openai_quota_title", { defaultValue: "Закончился баланс OpenAI" }),
      description: t("product_drawer.toast_openai_quota_desc", {
        defaultValue:
          "На балансе OpenAI закончились средства. Пополните баланс на platform.openai.com или укажите другой API-ключ в Настройках.",
      }),
      actionLabel: t("product_drawer.toast_openai_action_settings", { defaultValue: "Настройки" }),
      actionUrl: "/dashboard/settings",
    };
  }

  // 2. Invalid or revoked API key
  if (
    lower.includes("invalid_api_key") ||
    lower.includes("incorrect api key") ||
    lower.includes("недействительный") ||
    lower.includes("неверный api-ключ")
  ) {
    return {
      title: t("product_drawer.toast_openai_invalid_key_title", { defaultValue: "Недействительный ключ OpenAI" }),
      description: t("product_drawer.toast_openai_invalid_key_desc", {
        defaultValue: "Указанный API-ключ недействителен. Пожалуйста, проверьте ключ в Настройках профиля.",
      }),
      actionLabel: t("product_drawer.toast_openai_action_settings", { defaultValue: "Настройки" }),
      actionUrl: "/dashboard/settings",
    };
  }

  // 3. Rate Limit
  if (
    lower.includes("rate_limit") ||
    lower.includes("too many requests") ||
    lower.includes("превышен лимит запросов")
  ) {
    return {
      title: t("product_drawer.toast_openai_rate_limit_title", { defaultValue: "Превышен лимит запросов OpenAI" }),
      description: t("product_drawer.toast_openai_rate_limit_desc", {
        defaultValue: "Слишком много запросов за короткое время. Пожалуйста, подождите минуту перед повторной попыткой.",
      }),
    };
  }

  // 4. Missing API key
  if (lower.includes("openai_api_key_missing") || lower.includes("no openai api key provided")) {
    return {
      title: t("product_drawer.toast_openai_missing_title", { defaultValue: "Отсутствует ключ ChatGPT" }),
      description: t("product_drawer.toast_openai_missing_desc", {
        defaultValue: "Перейдите в настройки и укажите ваш OpenAI API ключ.",
      }),
      actionLabel: t("product_drawer.toast_openai_action_settings", { defaultValue: "Настройки" }),
      actionUrl: "/dashboard/settings",
    };
  }

  // 5. Try cleaning raw Python/JSON string if it has message: '...'
  let cleanMessage = text;
  const match = text.match(/['"]message['"]:\s*['"]([^'"]+)['"]/);
  if (match && match[1]) {
    cleanMessage = match[1];
  } else if (text.startsWith("Error code:")) {
    cleanMessage = text.replace(/^Error code:\s*\d+\s*-\s*/, "");
  }

  return {
    title: t("product_drawer.toast_openai_error_title", { defaultValue: "Ошибка API ChatGPT" }),
    description:
      cleanMessage && cleanMessage.length < 250
        ? cleanMessage
        : t("product_drawer.toast_openai_error_desc", {
            defaultValue: "Ошибка при обращении к OpenAI — проверьте баланс или лимиты.",
          }),
  };
}

export function showOpenAiErrorToast(
  rawError: string | undefined | null,
  t: (key: string, opts?: any) => string
) {
  const info = parseOpenAiError(rawError, t);
  const options: any = {
    id: "openai-error-toast",
    description: info.description,
    duration: 8000,
  };
  if (info.actionLabel && info.actionUrl) {
    options.action = {
      label: info.actionLabel,
      onClick: () => {
        if (typeof window !== "undefined") {
          window.location.href = info.actionUrl!;
        }
      },
    };
  }
  toast.error(info.title, options);
}
