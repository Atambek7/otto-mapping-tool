FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=10001:10001 . .

USER 10001:10001

EXPOSE 3301

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "3301", "--timeout-graceful-shutdown", "30"]
