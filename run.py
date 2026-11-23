"""Запуск сбора.

Примеры:
  python run.py
  python run.py --project capsuleos
  python run.py --core-only
  python run.py --probe
  python run.py --setup-drive
  python run.py --sync-only
"""

from __future__ import annotations

import argparse
import sys

from ff.client import AuthError
from ff.clusters import select_projects
from ff.drive import setup_oauth
from ff.runner import ROOT, configure_logging, load_settings, open_tools, run_clusters, _utf8_stdio


def main(argv: list[str] | None = None) -> int:
    _utf8_stdio()
    parser = argparse.ArgumentParser(description="Медленный сбор кейсов FastFounder по проектам")
    parser.add_argument("--project", action="append", help="slug проекта, можно несколько раз")
    parser.add_argument("--core-only", action="store_true", help="только ядро кластеров, без смежных запросов")
    parser.add_argument("--probe", action="store_true", help="один первый запрос, чтобы проверить сессию и разбор")
    parser.add_argument("--limit", type=int, default=30, choices=(10, 20, 30))
    parser.add_argument("--setup-drive", action="store_true", help="один раз войти в Google Диск")
    parser.add_argument("--sync-only", action="store_true", help="выгрузить уже собранное в папку на Диске")
    args = parser.parse_args(argv)

    configure_logging(ROOT)
    if args.setup_drive:
        setup_oauth(ROOT)
        return 0

    settings = load_settings(ROOT)
    store, client, drive = open_tools(ROOT, settings)
    try:
        if args.sync_only:
            store.export_all()
            drive.sync_database(store.db_path)
            drive.sync_file(store.jsonl_path, store.jsonl_path.name)
            drive.sync_file(store.csv_path, store.csv_path.name)
            for path in (ROOT / "export" / "md").glob("*.md"):
                drive.sync_file(path, f"md/{path.name}")
            print("Локальный экспорт обновлён. Если Диск подключён, файлы ушли в папку.")
            return 0
        projects = select_projects(args.project)
        run_clusters(
            store,
            client,
            drive,
            projects,
            include_adjacent=not args.core_only,
            limit=args.limit,
            probe=args.probe,
        )
    except AuthError as exc:
        print(exc)
        return 2
    except KeyboardInterrupt:
        print("Остановлено. Прогресс сохранён, следующий запуск продолжит с места остановки.")
        return 0
    finally:
        store.close()
    print(f"Готово. Локально: {ROOT / 'export'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
