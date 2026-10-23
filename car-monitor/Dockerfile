FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Moscow \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DB_PATH=data/data.db \
    BROWSER_PROFILE=data/browser_profile

# xvfb — виртуальный экран: браузер работает «с окном», как у живого человека
RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY . .
RUN chmod +x docker-entrypoint.sh

VOLUME /app/data
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "monitor.py"]
