"""Сбор кейсов по кластерам с продолжением после остановки."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from urllib.parse import urljoin
from dotenv import load_dotenv

from ff.client import AuthError, FetchError, PoliteClient
from ff.clusters import Project
from ff.parse import (
    ANACONDA,
    extract_article_links,
    form_is_stale,
    logged_out,
    parse_article,
    parse_company_cards,
    parse_search_token,
    search_form,
)
from ff.store import Store

log = logging.getLogger("ff.runner")

ROOT = Path(__file__).resolve().parents[1]


def build_jobs(projects: list[Project], include_adjacent: bool) -> list[tuple[str, str, str]]:
    jobs: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for project in projects:
        queries = list(project.core)
        if include_adjacent:
            queries.extend(project.adjacent)
        for query in queries:
            key = (project.title, query)
            if key in seen:
                continue
            seen.add(key)
            jobs.append((f"anaconda|{project.slug}|{query}", project.title, query))
    return jobs


def run_clusters(
    store: Store,
    client: PoliteClient,
    drive,
    projects: list[Project],
    include_adjacent: bool,
    limit: int,
    probe: bool,
) -> None:
    jobs = build_jobs(projects, include_adjacent)
    if probe:
        jobs = jobs[:1]
    from rich.console import Console
    from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

    console = Console()
    failures = 0
    paywall_locked = False
    saved_since_db_sync = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Кластеры", total=len(jobs))
        for job_key, project, query in jobs:
            if store.job_done(job_key):
                progress.advance(task)
                continue
            progress.update(task, description=f"{project}: {query[:42]}")
            try:
                created = _run_query(
                    store,
                    client,
                    drive,
                    project,
                    query,
                    limit,
                    paywall_locked,
                    follow=not probe,
                )
                paywall_locked = created["paywall_locked"]
                saved_since_db_sync += created["new"]
                store.mark_job(job_key, "done", created["note"])
                failures = 0
                console.print(
                    f"[green]{project}[/green] «{query}» — карточек {created['cards']}, "
                    f"новых {created['new']}, в базе {store.count()}"
                )
            except AuthError as exc:
                store.mark_job(job_key, "auth", str(exc)[:300])
                console.print(f"[red]{exc}[/red]")
                raise
            except FetchError as exc:
                failures += 1
                store.mark_job(job_key, "error", str(exc)[:300])
                log.error("Запрос не выполнен: %s", exc)
                console.print(f"[yellow]Пропуск «{query}»: {exc}[/yellow]")
                if failures >= 3:
                    console.print("[red]Три ошибки подряд. Останавливаюсь, чтобы не словить бан. Запусти снова позже.[/red]")
                    break
            if saved_since_db_sync >= 10:
                drive.sync_database(store.db_path)
                saved_since_db_sync = 0
            progress.advance(task)

    store.export_all()
    drive.sync_database(store.db_path)
    drive.sync_file(store.jsonl_path, store.jsonl_path.name)
    drive.sync_file(store.csv_path, store.csv_path.name)


def _search(client: PoliteClient, query: str, limit: int):
    # Страница без параметров кэшируется без сессии, поэтому форма и выдача идут через ff_live.
    form_url = ANACONDA + "?ff_live=1"
    response = client.post(form_url, search_form(query, limit), allow_redirects=False)
    if form_is_stale(response.text):
        nonce, referer = parse_search_token(response.text)
        if not nonce:
            raise FetchError("Сайт не выдал код формы. Обнови COOKIE и открой Анаконду в браузере.")
        response = client.post(form_url, search_form(query, limit, nonce, referer), allow_redirects=False)
    if response.status_code in {301, 302, 303, 307, 308}:
        location = urljoin(form_url, response.headers.get("Location", ""))
        if "ff_search=" in location and "ff_live=" not in location:
            location += "&ff_live=1"
        response = client.get(location)
    return response.text, response.url


def _run_query(
    store,
    client,
    drive,
    project: str,
    query: str,
    limit: int,
    paywall_locked: bool,
    follow: bool,
) -> dict:
    html, final_url = _search(client, query, limit)
    if logged_out(html):
        _dump(html)
        raise AuthError(
            "Анаконда открылась без входа. Обнови COOKIE в .env: зайди на сайт и скопируй cookie заново."
        )
    if form_is_stale(html):
        _dump(html)
        raise FetchError("Сайт отклонил форму поиска. Запусти этот запрос ещё раз.")
    cards = parse_company_cards(html)
    if not cards:
        _dump(html)
    new_count = 0
    for card in cards:
        card.setdefault("site_tags", [])
        card.setdefault("metrics", "")
        card.setdefault("section_essence", "")
        card.setdefault("section_insight", "")
        card.setdefault("section_action", "")
        card.setdefault("paywalled", False)
        if _save(store, drive, card, project, query):
            new_count += 1
        review_url = card.get("review_url") or ""
        if follow and review_url and not paywall_locked:
            paywall_locked = _fetch_article(store, client, drive, review_url, project, query, paywall_locked)

    if follow:
        for article_url in extract_article_links(html)[:8]:
            if paywall_locked:
                break
            paywall_locked = _fetch_article(store, client, drive, article_url, project, query, paywall_locked)

    return {
        "cards": len(cards),
        "new": new_count,
        "note": f"cards={len(cards)} url={final_url}",
        "paywall_locked": paywall_locked,
    }


def _fetch_article(store, client, drive, url: str, project: str, query: str, paywall_locked: bool) -> bool:
    existing = store.get_case(url)
    if existing and existing["full_text"] and not existing["paywalled"] and len(existing["full_text"]) > 800:
        _touch_tags(store, drive, existing, project, query)
        return paywall_locked
    response = client.get(url)
    if logged_out(response.text):
        log.warning("Полный обзор закрыт сессией, дальше качаю только карточки")
        return True
    article = parse_article(response.text)
    article["url"] = url
    article["source"] = "review"
    article["external_url"] = ""
    article["review_url"] = url
    _save(store, drive, article, project, query)
    if article["paywalled"]:
        log.warning("Текст обзора обрезан подпиской. Карточки Анаконды продолжаю, полные обзоры ставлю на паузу.")
        return True
    return paywall_locked


def _touch_tags(store, drive, existing, project: str, query: str) -> None:
    payload = {
        "url": existing["url"],
        "title": existing["title"],
        "source": existing["source"],
        "external_url": existing["external_url"],
        "review_url": existing["review_url"],
        "snippet": existing["snippet"],
        "full_text": existing["full_text"],
        "section_essence": existing["section_essence"],
        "section_insight": existing["section_insight"],
        "section_action": existing["section_action"],
        "round_info": existing["round_info"],
        "metrics": existing["metrics"],
        "site_tags": [],
        "paywalled": bool(existing["paywalled"]),
    }
    _save(store, drive, payload, project, query)


def _save(store: Store, drive, payload: dict, project: str, query: str) -> bool:
    _case_id, created = store.upsert_case(payload, project, query)
    row = store.get_case(payload["url"])
    if row is None:
        return False
    md_path = store.write_markdown(row)
    store.export_indexes()
    drive.sync_case_files(md_path, store.jsonl_path, store.csv_path)
    return created


def _dump(html: str) -> None:
    path = ROOT / "logs" / "last_anaconda.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def configure_logging(root: Path) -> None:
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "parser.log", encoding="utf-8"),
        ],
    )


def load_settings(root: Path) -> dict[str, str]:
    load_dotenv(root / ".env")
    import os

    return {
        "cookie": os.getenv("COOKIE", "").strip(),
        "folder_id": os.getenv("DRIVE_FOLDER_ID", "").strip(),
        "local_dir": os.getenv("DRIVE_LOCAL_DIR", "").strip(),
    }


def open_tools(root: Path, settings: dict[str, str]):
    from ff.drive import DriveSync

    store = Store(root / "data" / "fastfounder.sqlite", root / "export")
    drive = DriveSync(root, settings["folder_id"], settings["local_dir"])
    if not settings["cookie"]:
        store.close()
        raise SystemExit("В .env нет COOKIE. Скопируй сессию из браузера, где открыт fastfounder.ru.")
    client = PoliteClient(settings["cookie"])
    return store, client, drive


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")
