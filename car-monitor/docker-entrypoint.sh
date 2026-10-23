#!/bin/sh
# Виртуальный экран для браузера: сайты реже показывают капчу «оконному» Chromium
if [ -z "$DISPLAY" ]; then
    rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
    Xvfb :99 -screen 0 1366x900x24 -nolisten tcp >/dev/null 2>&1 &
    export DISPLAY=:99
    i=0
    while [ ! -e /tmp/.X11-unix/X99 ] && [ $i -lt 50 ]; do
        sleep 0.1
        i=$((i + 1))
    done
fi
exec "$@"
