import os
import sys

target = sys.argv[1] if len(sys.argv) > 1 else "backend.env"

keys = [
    "SECRET_KEY", "ADMIN_USERNAME", "ADMIN_PASSWORD", "CORS_ORIGINS", "COOKIE_SECURE", "PUBLIC_BASE_URL",
    "DATABASE_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND",
    "AFTERCOOL_USERNAME", "AFTERCOOL_PASSWORD", "AFTERCOOL_LOGIN_URL", "AFTERCOOL_BASE_URL",
    "OPENAI_API_KEY", "MODEL_NAME",
    "OTTO_JV_CLIENT_ID", "OTTO_JV_CLIENT_SECRET", "OTTO_JV_PARTNER_ID", "OTTO_JV_BRAND_ID", "OTTO_JV_APP_ID",
    "OTTO_XL_CLIENT_ID", "OTTO_XL_CLIENT_SECRET", "OTTO_XL_PARTNER_ID", "OTTO_XL_BRAND_ID", "OTTO_XL_APP_ID",
]

with open(target, "w", encoding="utf-8") as f:
    for k in keys:
        val = os.environ.get(k, "").strip()
        if val:
            f.write(f"{k}={val}\n")
