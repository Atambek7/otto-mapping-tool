import os
import sys

base_file = sys.argv[1] if len(sys.argv) > 1 else "backend/.env"
incoming_file = sys.argv[2] if len(sys.argv) > 2 else "backend/.env.incoming"

def read_env(path):
    data = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    data[k.strip()] = v.strip()
    return data

current = read_env(base_file)
incoming = read_env(incoming_file)

# Safe defaults if fresh setup
defaults = {
    "DATABASE_URL": "postgresql://otto_user:otto_pass@db:5432/otto_db",
    "CELERY_BROKER_URL": "redis://redis:6379/0",
    "CELERY_RESULT_BACKEND": "redis://redis:6379/0",
    "CORS_ORIGINS": "https://omt.automatonsoft.de,http://omt.automatonsoft.de,http://localhost:3000,http://127.0.0.1:3000,http://127.0.0.1:3200",
    "COOKIE_SECURE": "true",
    "MODEL_NAME": "gpt-4o-mini",
    "ADMIN_USERNAME": "yana",
    "AFTERCOOL_LOGIN_URL": "https://aftercool.de/auth/login",
    "AFTERCOOL_BASE_URL": "https://aftercool.de",
}

for k, v in defaults.items():
    if k not in current:
        current[k] = v

# Overlay non-empty incoming values from GitHub Secrets
for k, v in incoming.items():
    if v:
        current[k] = v

with open(base_file, "w", encoding="utf-8") as f:
    for k, v in current.items():
        f.write(f"{k}={v}\n")

os.chmod(base_file, 0o600)
print(f"Successfully updated {base_file}")
