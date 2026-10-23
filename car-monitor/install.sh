#!/bin/sh
# Установка: sh install.sh [ТОКЕН_БОТА]
set -e
cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
    echo "Docker не установлен. Установите его командой:"
    echo "  curl -fsSL https://get.docker.com | sh"
    exit 1
fi
if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    echo "Нет docker compose. Установите пакет docker-compose-plugin."
    exit 1
fi

[ -f .env ] || cp .env.example .env
TOKEN="$1"
if [ -z "$TOKEN" ] && ! grep -q '^TELEGRAM_BOT_TOKEN=..*' .env; then
    printf "Токен Telegram-бота от @BotFather: "
    read -r TOKEN
fi
if [ -n "$TOKEN" ]; then
    sed -i "s|^TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=$TOKEN|" .env
fi
if ! grep -q '^TELEGRAM_BOT_TOKEN=..*' .env; then
    echo "Токен не задан. Впишите его в файл .env и запустите install.sh ещё раз."
    exit 1
fi

mkdir -p data
$COMPOSE up -d --build

echo
echo "Готово! Бот запущен в Docker и будет сам подниматься после перезагрузки сервера."
echo "Напишите своему боту в Telegram /start (первый написавший становится владельцем)."
echo
echo "Логи:        $COMPOSE logs -f"
echo "Остановить:  $COMPOSE down"
echo "Обновить:    распакуйте новый архив поверх этой папки и снова запустите sh install.sh"
