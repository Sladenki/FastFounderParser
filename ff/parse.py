"""Разбор HTML Анаконды, карточек компаний и полного текста обзора."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

BASE = "https://fastfounder.ru"
ANACONDA = f"{BASE}/anaconda3/"

RESERVED = {
    "news",
    "deals",
    "anaconda3",
    "srch",
    "tag",
    "wp-login.php",
    "feed",
    "offer",
    "privacy-policy",
    "pay_security",
    "howtoread",
    "prepayment",
    "misc",
    "author",
    "category",
    "page",
    "search",
    "xmlrpc.php",
    "wp-json",
    "wp-content",
    "wp-admin",
    "comments",
    "about",
}

SECTION_MARKERS = (
    ("section_essence", "Суть проекта"),
    ("section_insight", "Что интересного"),
    ("section_action", "Куда бежать"),
)

METRIC_RE = re.compile(
    r".{0,90}(?:\$\s?\d[\d.,]*\s?[KMB]?|€\s?\d[\d.,]*|£\s?\d[\d.,]*|\d+(?:[.,]\d+)?\s?%|MRR|ARR|выручк\w*|юнит-эконом\w*).{0,90}",
    re.IGNORECASE,
)

LOGGED_OUT = "Вы не вошли в сервис"
PAYWALL = "чтобы увидеть полный текст"


def canon_url(url: str) -> str:
    absolute = urljoin(BASE, url.strip())
    parsed = urlparse(absolute)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
    ]
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return urlunparse((parsed.scheme.lower() or "https", parsed.netloc.lower(), path, "", urlencode(query), ""))


def is_article_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc not in {"fastfounder.ru", "www.fastfounder.ru"}:
        return False
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 1:
        return False
    slug = parts[0].lower()
    if slug in RESERVED or "." in slug:
        return False
    return True


def is_external(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.netloc.lower()
    if not host or "fastfounder." in host:
        return False
    blocked = ("wp-login", "mail.ru", "w.org", "googleapis.", "gstatic.")
    return not any(piece in host for piece in blocked)


def logged_out(html: str) -> bool:
    return LOGGED_OUT in html


def parse_company_cards(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    cards: list[dict] = []
    for article in soup.select("div.article"):
        if article.find("form"):
            cards.extend(_loose_cards(article))
            continue
        card = _card_from_block(article)
        if card:
            cards.append(card)
    if not cards:
        root = soup.select_one("#ff-taxonomy-results") or soup.select_one(".entry-content")
        if root is not None:
            cards.extend(_loose_cards(root))
    return _dedupe(cards)


def extract_article_links(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(".content-area") or soup.body
    if root is None:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for anchor in root.find_all("a", href=True):
        if anchor.find_parent("header") or anchor.find_parent("footer"):
            continue
        url = canon_url(anchor["href"])
        if is_article_url(url) and url not in seen:
            seen.add(url)
            found.append(url)
    return found


def parse_article(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.select_one("h1")
    title = heading.get_text(" ", strip=True) if heading else ""
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True).replace("— FastFounder", "").replace("- FastFounder", "").strip()
    content = soup.select_one(".entry-content")
    if content is None:
        return {
            "title": title,
            "full_text": "",
            "snippet": "",
            "site_tags": [],
            "paywalled": False,
            "missing": True,
            "section_essence": "",
            "section_insight": "",
            "section_action": "",
            "metrics": "",
            "round_info": "",
        }
    for node in content.select("script, style"):
        node.decompose()
    full_text = content.get_text("\n", strip=True)
    tags = []
    for anchor in soup.select('a[href*="/tag/"]'):
        label = anchor.get_text(" ", strip=True).lstrip("#").strip()
        if label and label not in tags:
            tags.append(label)
    sections = _split_sections(full_text)
    paywalled = PAYWALL in full_text
    round_info = _round_line(full_text)
    return {
        "title": title,
        "full_text": full_text,
        "snippet": _snippet(full_text),
        "site_tags": tags,
        "paywalled": paywalled,
        "metrics": _metrics(full_text),
        "round_info": round_info,
        **sections,
    }


STALE_FORM = "Форма поиска устарела"


def search_form(query: str, limit: int = 30, nonce: str = "", referer: str = "/anaconda3/") -> dict[str, str]:
    payload = {
        "query": query[:1200],
        "limit": str(limit if limit in {10, 20, 30} else 30),
        "language": "ru",
        "source_result": "",
        "navigation_state": "null",
        "without_screenshots": "1",
        "_wp_http_referer": referer or "/anaconda3/",
    }
    if nonce:
        payload["ff_anaconda_nonce"] = nonce
    return payload


def parse_search_token(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    nonce = soup.select_one('input[name="ff_anaconda_nonce"]')
    referer = soup.select_one('input[name="_wp_http_referer"]')
    nonce_value = nonce.get("value", "").strip() if nonce else ""
    referer_value = referer.get("value", "").strip() if referer else "/anaconda3/"
    return nonce_value, referer_value


def form_is_stale(html: str) -> bool:
    return STALE_FORM in html


def _card_from_block(block) -> dict | None:
    anchor = None
    for candidate in block.find_all("a", href=True):
        if is_external(candidate["href"]):
            anchor = candidate
            break
    if anchor is None:
        return None
    title = anchor.get_text(" ", strip=True)
    if not title or len(title) > 120:
        return None
    lines = _text_lines(block)
    return _pack_card(title, canon_url(anchor["href"]), lines, _review_links(block))


def _loose_cards(root) -> list[dict]:
    cards = []
    for anchor in root.find_all("a", href=True):
        if anchor.find_parent("header") or anchor.find_parent("footer") or anchor.find_parent("form"):
            continue
        href = anchor["href"]
        if not is_external(href):
            continue
        title = anchor.get_text(" ", strip=True)
        if not title or len(title) > 80:
            continue
        block = anchor
        lines: list[str] = []
        for _ in range(5):
            block = block.parent
            if block is None or getattr(block, "name", None) in {"body", "html"}:
                break
            lines = _text_lines(block)
            if any(len(line) > 50 for line in lines):
                break
        card = _pack_card(title, canon_url(href), lines, _review_links(block or anchor))
        if card and (card["snippet"] or card["round_info"]):
            cards.append(card)
    return cards


def _pack_card(title: str, external_url: str, lines: list[str], review_urls: list[str]) -> dict | None:
    round_info = ""
    description = ""
    for line in lines:
        if line == title:
            continue
        lowered = line.lower()
        if "раунд" in lowered or "всего:" in lowered or line.startswith("$"):
            if not round_info:
                round_info = line
            continue
        if len(line) > len(description):
            description = line
    if len(description) < 40 and not round_info:
        return None
    review_url = review_urls[0] if review_urls else ""
    case_url = review_url or external_url
    return {
        "url": case_url,
        "title": title,
        "external_url": external_url,
        "review_url": review_url,
        "snippet": description,
        "round_info": round_info,
        "full_text": description,
        "source": "anaconda",
    }


def _review_links(node) -> list[str]:
    if node is None:
        return []
    found = []
    for anchor in node.find_all("a", href=True):
        url = canon_url(anchor["href"])
        if is_article_url(url) and url not in found:
            found.append(url)
    return found


def _text_lines(node) -> list[str]:
    text = node.get_text("\n", strip=True)
    return [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]


def _dedupe(cards: list[dict]) -> list[dict]:
    unique = []
    seen: set[str] = set()
    for card in cards:
        key = card["url"]
        if key in seen:
            continue
        seen.add(key)
        unique.append(card)
    return unique


def _split_sections(text: str) -> dict[str, str]:
    positions = []
    for key, marker in SECTION_MARKERS:
        index = text.find(marker)
        if index >= 0:
            positions.append((index, key, marker))
    positions.sort()
    sections = {key: "" for key, _marker in SECTION_MARKERS}
    for pos, (index, key, marker) in enumerate(positions):
        end = positions[pos + 1][0] if pos + 1 < len(positions) else len(text)
        sections[key] = text[index + len(marker) : end].strip(" \n:—-")
    return sections


def _snippet(text: str, limit: int = 500) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:limit]


def _round_line(text: str) -> str:
    for line in text.splitlines():
        if "раунд" in line.lower() or "Стартап получил" in line:
            return re.sub(r"\s+", " ", line).strip()
    return ""


def _metrics(text: str) -> str:
    found = []
    for match in METRIC_RE.findall(text):
        line = re.sub(r"\s+", " ", match).strip()
        if line and line not in found:
            found.append(line)
        if len(found) >= 8:
            break
    return "\n".join(found)
