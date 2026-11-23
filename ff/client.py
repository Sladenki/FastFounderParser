"""HTTP-клиент с паузами, сменой User-Agent и ожиданием при 429/5xx."""

from __future__ import annotations

import logging
import random
import time

import requests

log = logging.getLogger("ff.client")

USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
)

BLOCK_HINTS = (
    "слишком много запросов",
    "too many requests",
    "cf-browser-verification",
    "attention required",
    "доступ ограничен",
)

RETRY_STATUSES = {429, 500, 502, 503, 504}


class AuthError(RuntimeError):
    """Сессия FastFounder не принята."""


class FetchError(RuntimeError):
    """Запрос не удался после повторов."""


class PoliteClient:
    def __init__(self, cookie: str) -> None:
        self.cookie = cookie.strip()
        if not self.cookie:
            raise AuthError("В .env пустая COOKIE.")
        self.session = requests.Session()
        for part in self.cookie.split(";"):
            name, value = part.strip().split("=", 1)
            self.session.cookies.set(name, value, domain="fastfounder.ru", path="/")
        self.completed = 0

    def get(self, url: str) -> requests.Response:
        return self._send("GET", url)

    def post(self, url: str, data: dict[str, str], allow_redirects: bool = True) -> requests.Response:
        return self._send("POST", url, data=data, allow_redirects=allow_redirects)

    def _send(self, method: str, url: str, allow_redirects: bool = True, **kwargs) -> requests.Response:
        self._wait_turn()
        delay = 30.0
        last_error = "неизвестная ошибка"
        for attempt in range(1, 6):
            headers = {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru,en;q=0.8",
                "Referer": "https://fastfounder.ru/anaconda3/",
            }
            try:
                response = self.session.request(
                    method,
                    url,
                    headers=headers,
                    timeout=45,
                    allow_redirects=allow_redirects,
                    **kwargs,
                )
            except requests.RequestException as exc:
                last_error = exc.__class__.__name__
                log.warning("Сеть: %s, попытка %s, пауза %.0f с", last_error, attempt, delay)
                time.sleep(delay + random.uniform(1, 5))
                delay = min(delay * 2, 900)
                continue

            if response.status_code in RETRY_STATUSES:
                last_error = f"HTTP {response.status_code}"
                pause = self._retry_after(response, delay)
                log.warning("%s на %s, пауза %.0f с", last_error, url, pause)
                time.sleep(pause)
                delay = min(delay * 2, 900)
                continue

            if response.status_code in {401, 403}:
                raise AuthError(f"Сайт вернул {response.status_code}. Обнови COOKIE в .env и зайди на сайт заново.")

            if response.status_code >= 400:
                raise FetchError(f"HTTP {response.status_code} для {url}")

            body_head = response.text[:4000].lower()
            if any(hint in body_head for hint in BLOCK_HINTS):
                last_error = "страница с ограничением"
                pause = self._retry_after(response, delay)
                log.warning("Похоже на ограничение доступа, пауза %.0f с", pause)
                time.sleep(pause)
                delay = min(delay * 2, 900)
                continue

            self.completed += 1
            return response

        raise FetchError(f"Не удалось получить {url}: {last_error}")

    def _wait_turn(self) -> None:
        if self.completed == 0:
            return
        delay = random.uniform(5, 15)
        if self.completed % 8 == 0:
            extra = random.uniform(45, 90)
            delay += extra
            log.info("Длинная пауза %.0f с после %s запросов", delay, self.completed)
        else:
            log.info("Пауза %.1f с", delay)
        time.sleep(delay)

    @staticmethod
    def _retry_after(response: requests.Response, fallback: float) -> float:
        raw = response.headers.get("Retry-After", "")
        try:
            hinted = float(raw)
        except ValueError:
            hinted = 0
        return max(fallback, hinted) + random.uniform(1, 5)
