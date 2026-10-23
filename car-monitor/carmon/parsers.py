"""Разбор страниц выдачи Avito, Авто.ру и Drom в список объявлений."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup


@dataclass
class Listing:
    source: str
    id: str
    title: str
    url: str
    price: int | None = None
    location: str = ""
    date: str = ""
    info: str = ""
    image: str | None = None


SOURCE_NAMES = {"avito": "Avito", "autoru": "Авто.ру", "drom": "Drom"}

# Ссылки на карточки объявлений — запасной способ, если вёрстка поменялась.
AD_LINK_RE = {
    "avito": re.compile(r"/avtomobili/[^/?#\"]*_(\d{6,})(?:[?#\"]|$)"),
    "autoru": re.compile(r"/cars/(?:used|new)/sale/[^\"?#]*?/(\d+-[0-9a-f]+)/"),
    "drom": re.compile(r"drom\.ru/[^\"?#]+/(\d{5,})\.html"),
}


def detect_source(url: str) -> str | None:
    host = urlsplit(url).netloc.lower()
    if host.endswith("avito.ru"):
        return "avito"
    if host.endswith("auto.ru"):
        return "autoru"
    if host.endswith("drom.ru"):
        return "drom"
    return None


def parse(source: str, html: str, page_url: str) -> list[Listing]:
    soup = BeautifulSoup(html, "lxml")
    items = PARSERS[source](soup, page_url)
    if not items:
        items = _parse_links(source, soup, page_url)
    # Одно объявление может встречаться на странице дважды (VIP-блок и т.п.)
    unique: dict[str, Listing] = {}
    for item in items:
        unique.setdefault(item.id, item)
    return list(unique.values())


def parse_price(text: str | None) -> int | None:
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else None


def _text(node, selector: str, sep: str = " ") -> str:
    el = node.select_one(selector)
    if not el:
        return ""
    return _clean(el.get_text(sep, strip=True))


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text.replace("\xa0", " "))
    return re.sub(r"\s+([,.])", r"\1", text).strip()


def _strip_query(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _image(node) -> str | None:
    img = node.select_one("img")
    if not img:
        return None
    src = img.get("src") or img.get("data-src")
    if not src or src.startswith("data:"):
        return None
    return "https:" + src if src.startswith("//") else src


def _parse_avito(soup, page_url):
    cards = soup.select('[data-marker="catalog-serp"] [data-marker="item"]') or soup.select(
        '[data-marker="item"]'
    )
    result = []
    for card in cards:
        item_id = card.get("data-item-id")
        link = card.select_one('[data-marker="item-title"]')
        if not item_id or not link or not link.get("href"):
            continue
        price_meta = card.select_one('meta[itemprop="price"]')
        price = parse_price(price_meta.get("content")) if price_meta else None
        if price is None:
            price = parse_price(_text(card, '[data-marker="item-price"]'))
        result.append(
            Listing(
                source="avito",
                id=item_id,
                title=_clean(link.get_text(" ", strip=True)),
                url=_strip_query(urljoin(page_url, link["href"])),
                price=price,
                location=_text(card, '[data-marker="item-location"]'),
                date=_text(card, '[data-marker="item-date"]'),
                info=_text(card, '[data-marker="item-specific-params"]'),
                image=_image(card),
            )
        )
    return result


def _parse_autoru(soup, page_url):
    result = []
    for card in soup.select('[data-seo="listing-item"], .ListingItem'):
        link = card.select_one("a.ListingItemTitle__link") or card.find(
            "a", href=AD_LINK_RE["autoru"]
        )
        if not link:
            continue
        url = urljoin(page_url, link["href"])
        m = AD_LINK_RE["autoru"].search(url)
        if not m:
            continue
        price = parse_price(
            _text(card, '[class*="ListingItemUniversalPrice__title"]')
            or _text(card, ".ListingItemPrice__content")
        )
        info = " · ".join(
            t
            for t in (
                _text(card, '[class*="ListingItemUniversalCondition"]', ", "),
                _text(card, '[class*="ListingItemUniversalSpecs__specs"]', ", "),
            )
            if t
        )
        result.append(
            Listing(
                source="autoru",
                id=m.group(1),
                title=_clean(link.get_text(" ", strip=True)),
                url=_strip_query(url),
                price=price,
                location=_text(card, '[class*="ListingItemUniversalSeller__sellerAddress"]')
                or _text(card, ".MetroListPlace__regionName"),
                info=info,
                image=_image(card),
            )
        )
    return result


def _parse_drom(soup, page_url):
    result = []
    for card in soup.select('[data-ftid="bulls-list_bull"]'):
        link = card.select_one('a[data-ftid="bull_title"]')
        if not link or not link.get("href"):
            continue
        url = urljoin(page_url, link["href"])
        m = AD_LINK_RE["drom"].search(url)
        if not m:
            continue
        title = _clean(link.get_text(" ", strip=True))
        subtitle = _text(card, '[data-ftid="bull_subtitle"]')
        if subtitle:
            title = f"{title} {subtitle}"
        result.append(
            Listing(
                source="drom",
                id=m.group(1),
                title=title,
                url=_strip_query(url),
                price=parse_price(_text(card, '[data-ftid="bull_price"]')),
                location=_text(card, '[data-ftid="bull_location"]'),
                date=_text(card, '[data-ftid="bull_date"]'),
                info=_text(card, '[data-ftid="component_inline-bull-description"]'),
                image=_image(card),
            )
        )
    return result


def _parse_links(source, soup, page_url):
    """Запасной разбор: просто все ссылки на объявления на странице."""
    result = []
    for a in soup.find_all("a", href=True):
        url = urljoin(page_url, a["href"])
        m = AD_LINK_RE[source].search(url)
        title = _clean(a.get_text(" ", strip=True))
        if m and title:
            result.append(Listing(source=source, id=m.group(1), title=title, url=_strip_query(url)))
    return result


PARSERS = {"avito": _parse_avito, "autoru": _parse_autoru, "drom": _parse_drom}
