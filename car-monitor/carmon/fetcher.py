"""Загрузка страниц выдачи.

Avito и Авто.ру отдают обычным HTTP-клиентам капчу или ошибку 429, поэтому
их страницы открываются в настоящем браузере Chromium (Playwright) с постоянным
профилем: куки сохраняются между запусками, а капчу при необходимости можно
один раз решить руками в окне браузера. Drom нормально отвечает на обычные
запросы, браузер для него — запасной вариант.
"""
from __future__ import annotations

import logging
import random
from pathlib import Path

import requests
from bs4 import UnicodeDammit

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
}
BROWSER_FIRST = {"avito", "autoru"}
CARD_SELECTOR = {
    "avito": '[data-marker="item"]',
    "autoru": '[data-seo="listing-item"]',
    "drom": '[data-ftid="bulls-list_bull"]',
}
BLOCK_MARKERS = ("showcaptcha", "Доступ ограничен", "Проблема с IP", "firewall-container")


class FetchError(Exception):
    pass


class Fetcher:
    def __init__(
        self,
        profile_dir: Path,
        headless: bool = False,
        channel: str = "",
        proxy: str = "",
        browser_options: dict | None = None,
    ):
        self.profile_dir = profile_dir
        self.headless = headless
        self.channel = channel
        self.proxy = proxy
        self.browser_options = browser_options or {}
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}
        self._pw = None
        self._context = None
        self._page = None
        self._browser_missing = False

    # --- публичный интерфейс -------------------------------------------------
    def get(self, url: str, source: str) -> str:
        if source in BROWSER_FIRST and self._browser_available():
            return self._get_browser(url, source)
        try:
            return self._get_http(url)
        except FetchError as exc:
            if not self._browser_available():
                raise
            log.info("HTTP не сработал (%s), открываю в браузере", exc)
            return self._get_browser(url, source)

    def close(self) -> None:
        for obj, method in ((self._context, "close"), (self._pw, "stop")):
            if obj is not None:
                try:
                    getattr(obj, method)()
                except Exception:  # браузер мог уже упасть
                    pass
        self._pw = self._context = self._page = None

    # --- HTTP ----------------------------------------------------------------
    def _get_http(self, url: str) -> str:
        try:
            resp = self.session.get(url, timeout=30)
        except requests.RequestException as exc:
            raise FetchError(f"сеть: {exc}") from exc
        if resp.status_code != 200:
            raise FetchError(f"HTTP {resp.status_code}")
        html = UnicodeDammit(resp.content, is_html=True).unicode_markup
        _check_blocked(resp.url, html)
        return html

    # --- браузер -------------------------------------------------------------
    def _browser_available(self) -> bool:
        if self._browser_missing:
            return False
        try:
            import playwright  # noqa: F401
        except ImportError:
            log.warning("Playwright не установлен — Avito и Авто.ру, скорее всего, не откроются")
            self._browser_missing = True
            return False
        return True

    def _ensure_page(self):
        if self._page is not None and not self._page.is_closed():
            return self._page
        self.close()
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        options = dict(
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={"width": 1366, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        if self.channel:
            options["channel"] = self.channel
        if self.proxy:
            options["proxy"] = {"server": self.proxy}
        options.update(self.browser_options)
        try:
            self._context = self._pw.chromium.launch_persistent_context(**options)
        except Exception as exc:
            self.close()
            raise FetchError(
                f"не удалось запустить браузер: {exc}. "
                "Выполните: python -m playwright install chromium"
            ) from exc
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        # Картинки и шрифты не нужны: ссылки на фото и так есть в HTML, а страница грузится быстрее
        self._page.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in ("image", "media", "font")
            else route.continue_(),
        )
        return self._page

    def _get_browser(self, url: str, source: str) -> str:
        try:
            page = self._ensure_page()
            for attempt in range(2):
                resp = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                try:
                    page.wait_for_selector(CARD_SELECTOR[source], timeout=15_000)
                except Exception:
                    pass  # пустая выдача или капча — разберёмся ниже
                html = page.content()
                final_url = page.url
                blocked = (resp is not None and resp.status in (403, 429, 439)) or _is_blocked(
                    final_url, html
                )
                if not blocked or attempt:
                    break
                # Антибот часто пускает со второго раза, когда его скрипт поставил куки
                log.info("%s проверяет браузер, повторяю через несколько секунд", source)
                page.wait_for_timeout(random.uniform(4000, 8000))
        except FetchError:
            raise
        except Exception as exc:
            self.close()  # при следующей проверке браузер перезапустится
            raise FetchError(f"браузер: {exc}") from exc
        if resp is not None and resp.status >= 400 and resp.status != 404:
            _check_blocked(final_url, html)
            raise FetchError(f"HTTP {resp.status}")
        _check_blocked(final_url, html)
        return html


def _is_blocked(url: str, html: str) -> bool:
    head = html[:20_000]
    return any(marker in url or marker in head for marker in BLOCK_MARKERS)


def _check_blocked(url: str, html: str) -> None:
    if _is_blocked(url, html):
        raise FetchError("сайт показал капчу / ограничил доступ")
