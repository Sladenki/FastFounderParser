"""SQLite, checkpoint и экспорт JSONL / CSV / Markdown после каждой записи."""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Store:
    def __init__(self, db_path: Path, export_dir: Path) -> None:
        self.db_path = db_path
        self.export_dir = export_dir
        self.md_dir = export_dir / "md"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.md_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init()

    def close(self) -> None:
        self.conn.close()

    def _init(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY,
                url TEXT NOT NULL UNIQUE,
                title TEXT,
                source TEXT,
                external_url TEXT,
                review_url TEXT,
                snippet TEXT,
                full_text TEXT,
                section_essence TEXT,
                section_insight TEXT,
                section_action TEXT,
                round_info TEXT,
                metrics TEXT,
                site_tags TEXT,
                project_tags TEXT,
                queries TEXT,
                paywalled INTEGER DEFAULT 0,
                parsed_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS jobs (
                job_key TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                note TEXT,
                updated_at TEXT
            );
            """
        )
        self.conn.commit()

    def job_done(self, job_key: str) -> bool:
        row = self.conn.execute("SELECT status FROM jobs WHERE job_key = ?", (job_key,)).fetchone()
        return bool(row and row["status"] == "done")

    def job_note(self, job_key: str) -> str:
        row = self.conn.execute("SELECT note FROM jobs WHERE job_key = ?", (job_key,)).fetchone()
        if row is None or not row["note"]:
            return ""
        return str(row["note"])

    def mark_job(self, job_key: str, status: str, note: str = "") -> None:
        self.conn.execute(
            """
            INSERT INTO jobs (job_key, status, note, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(job_key) DO UPDATE SET
                status = excluded.status,
                note = excluded.note,
                updated_at = excluded.updated_at
            """,
            (job_key, status, note[:500], now()),
        )
        self.conn.commit()

    def get_case(self, url: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM cases WHERE url = ?", (url,)).fetchone()

    def upsert_case(self, payload: dict, project: str, query: str) -> tuple[int, bool]:
        current = self.get_case(payload["url"])
        stamp = now()
        projects = _merge(_loads(current["project_tags"]) if current else [], [project] if project else [])
        queries = _merge(_loads(current["queries"]) if current else [], [query] if query else [])
        tags = _merge(_loads(current["site_tags"]) if current else [], payload.get("site_tags") or [])
        created = current is None
        full_text = _prefer(payload.get("full_text"), current["full_text"] if current else "")
        if "paywalled" in payload:
            paywalled = 1 if payload.get("paywalled") else 0
        elif current:
            paywalled = int(current["paywalled"] or 0)
        else:
            paywalled = 0
        self.conn.execute(
            """
            INSERT INTO cases (
                url, title, source, external_url, review_url, snippet, full_text,
                section_essence, section_insight, section_action, round_info, metrics,
                site_tags, project_tags, queries, paywalled, parsed_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                title = COALESCE(NULLIF(excluded.title, ''), cases.title),
                source = CASE
                    WHEN excluded.source = '' THEN cases.source
                    WHEN cases.source = '' OR cases.source IS NULL THEN excluded.source
                    WHEN instr(cases.source, excluded.source) > 0 THEN cases.source
                    ELSE cases.source || '+' || excluded.source
                END,
                external_url = COALESCE(NULLIF(excluded.external_url, ''), cases.external_url),
                review_url = COALESCE(NULLIF(excluded.review_url, ''), cases.review_url),
                snippet = CASE
                    WHEN length(excluded.snippet) > length(COALESCE(cases.snippet, '')) THEN excluded.snippet
                    ELSE cases.snippet END,
                full_text = CASE
                    WHEN length(excluded.full_text) > length(COALESCE(cases.full_text, '')) THEN excluded.full_text
                    ELSE cases.full_text END,
                section_essence = COALESCE(NULLIF(excluded.section_essence, ''), cases.section_essence),
                section_insight = COALESCE(NULLIF(excluded.section_insight, ''), cases.section_insight),
                section_action = COALESCE(NULLIF(excluded.section_action, ''), cases.section_action),
                round_info = COALESCE(NULLIF(excluded.round_info, ''), cases.round_info),
                metrics = CASE
                    WHEN length(excluded.metrics) > length(COALESCE(cases.metrics, '')) THEN excluded.metrics
                    ELSE cases.metrics END,
                site_tags = excluded.site_tags,
                project_tags = excluded.project_tags,
                queries = excluded.queries,
                paywalled = excluded.paywalled,
                updated_at = excluded.updated_at
            """,
            (
                payload["url"],
                payload.get("title") or "",
                payload.get("source") or "",
                payload.get("external_url") or "",
                payload.get("review_url") or "",
                payload.get("snippet") or "",
                full_text,
                payload.get("section_essence") or "",
                payload.get("section_insight") or "",
                payload.get("section_action") or "",
                payload.get("round_info") or "",
                payload.get("metrics") or "",
                json.dumps(tags, ensure_ascii=False),
                json.dumps(projects, ensure_ascii=False),
                json.dumps(queries, ensure_ascii=False),
                paywalled,
                current["parsed_at"] if current else stamp,
                stamp,
            ),
        )
        self.conn.commit()
        row = self.get_case(payload["url"])
        assert row is not None
        return int(row["id"]), created

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0])

    def all_cases(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM cases ORDER BY id").fetchall())

    def export_indexes(self) -> None:
        rows = self.all_cases()
        self._write_jsonl(rows)
        self._write_csv(rows)

    def export_all(self) -> None:
        rows = self.all_cases()
        self._write_jsonl(rows)
        self._write_csv(rows)
        for row in rows:
            self.write_markdown(row)

    def write_markdown(self, row: sqlite3.Row) -> Path:
        path = self.md_dir / f"{row['id']:05d}-{_slug(row['title'] or row['url'])}.md"
        projects = ", ".join(_loads(row["project_tags"])) or "—"
        queries = ", ".join(_loads(row["queries"])) or "—"
        tags = ", ".join(_loads(row["site_tags"])) or "—"
        parts = [
            f"# {row['title'] or row['url']}",
            "",
            f"- URL: {row['url']}",
            f"- Сайт компании: {row['external_url'] or '—'}",
            f"- Обзор: {row['review_url'] or '—'}",
            f"- Источник: {row['source']}",
            f"- Проекты: {projects}",
            f"- Запросы: {queries}",
            f"- Теги сайта: {tags}",
            f"- Раунд: {row['round_info'] or '—'}",
            f"- Снято: {row['updated_at']}",
            "",
        ]
        if row["section_essence"]:
            parts.extend(["## Суть проекта", "", row["section_essence"], ""])
        if row["section_insight"]:
            parts.extend(["## Что интересного", "", row["section_insight"], ""])
        if row["section_action"]:
            parts.extend(["## Куда бежать", "", row["section_action"], ""])
        if row["metrics"]:
            parts.extend(["## Метрики", "", row["metrics"], ""])
        parts.extend(["## Текст", "", row["full_text"] or row["snippet"] or "", ""])
        path.write_text("\n".join(parts), encoding="utf-8")
        return path

    def markdown_path(self, row: sqlite3.Row) -> Path:
        return self.md_dir / f"{row['id']:05d}-{_slug(row['title'] or row['url'])}.md"

    @property
    def jsonl_path(self) -> Path:
        return self.export_dir / "cases.jsonl"

    @property
    def csv_path(self) -> Path:
        return self.export_dir / "cases.csv"

    def _write_jsonl(self, rows: list[sqlite3.Row]) -> None:
        with self.jsonl_path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(_public_row(row), ensure_ascii=False) + "\n")

    def _write_csv(self, rows: list[sqlite3.Row]) -> None:
        fieldnames = list(_public_row(rows[0]).keys()) if rows else list(_empty_public().keys())
        with self.csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(_public_row(row))


def _loads(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _merge(left: list[str], right: list[str]) -> list[str]:
    merged = list(left)
    for item in right:
        if item and item not in merged:
            merged.append(item)
    return merged


def _prefer(new: str | None, old: str | None) -> str:
    new = new or ""
    old = old or ""
    return new if len(new) >= len(old) else old


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^\w\-]+", "-", value, flags=re.UNICODE).strip("-").lower()
    return (cleaned or "case")[:60]


def _public_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "url": row["url"],
        "title": row["title"],
        "source": row["source"],
        "external_url": row["external_url"],
        "review_url": row["review_url"],
        "project_tags": ", ".join(_loads(row["project_tags"])),
        "queries": ", ".join(_loads(row["queries"])),
        "site_tags": ", ".join(_loads(row["site_tags"])),
        "round_info": row["round_info"],
        "metrics": row["metrics"],
        "paywalled": bool(row["paywalled"]),
        "snippet": row["snippet"],
        "section_essence": row["section_essence"],
        "section_insight": row["section_insight"],
        "section_action": row["section_action"],
        "full_text": row["full_text"],
        "parsed_at": row["parsed_at"],
        "updated_at": row["updated_at"],
    }


def _empty_public() -> dict:
    return {
        "id": "",
        "url": "",
        "title": "",
        "source": "",
        "external_url": "",
        "review_url": "",
        "project_tags": "",
        "queries": "",
        "site_tags": "",
        "round_info": "",
        "metrics": "",
        "paywalled": "",
        "snippet": "",
        "section_essence": "",
        "section_insight": "",
        "section_action": "",
        "full_text": "",
        "parsed_at": "",
        "updated_at": "",
    }
