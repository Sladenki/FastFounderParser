"""Копия экспорта в папку Google Диска: локальная синхронизация или API."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

log = logging.getLogger("ff.drive")

SCOPES = ["https://www.googleapis.com/auth/drive"]
FOLDER_ID_DEFAULT = "1nAw_Zoowbmv5vIeiidHWEaE3KD-m_ZjH"


class DriveSync:
    def __init__(self, root: Path, folder_id: str, local_dir: str = "") -> None:
        self.root = root
        self.folder_id = folder_id or FOLDER_ID_DEFAULT
        self.local_dir = Path(local_dir) if local_dir else None
        self.index_path = root / "data" / "drive_index.json"
        self.index = self._load_index()
        self.service = None
        self.ready = False
        self.warned = False
        self.md_folder_id = self.index.get("__md_folder__", "")
        self._bind()

    def _bind(self) -> None:
        if self.local_dir and self.local_dir.exists():
            (self.local_dir / "md").mkdir(parents=True, exist_ok=True)
            self.ready = True
            log.info("Google Диск: локальная папка %s", self.local_dir)
            return
        token = self.root / "token.json"
        if not token.exists():
            return
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            creds = Credentials.from_authorized_user_file(str(token), SCOPES)
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                token.write_text(creds.to_json(), encoding="utf-8")
            if creds.valid:
                self.service = build("drive", "v3", credentials=creds)
                self.ready = True
                log.info("Google Диск: API, папка %s", self.folder_id)
        except Exception as exc:  # noqa: BLE001 — хотим продолжить сбор, даже если Диск недоступен
            log.warning("Google Диск не подключился: %s", exc.__class__.__name__)

    def sync_file(self, path: Path, remote_name: str | None = None) -> None:
        if not path.exists():
            return
        if not self.ready:
            self._warn_once()
            return
        name = remote_name or path.name
        try:
            if self.local_dir:
                self._copy_local(path, name)
            elif self.service is not None:
                self._upload(path, name)
        except Exception as exc:  # noqa: BLE001
            log.warning("Не удалось выгрузить %s: %s", name, exc.__class__.__name__)

    def sync_case_files(self, md_path: Path, jsonl: Path, csv_path: Path) -> None:
        self.sync_file(md_path, f"md/{md_path.name}")
        self.sync_file(jsonl, jsonl.name)
        self.sync_file(csv_path, csv_path.name)

    def sync_database(self, db_path: Path) -> None:
        self.sync_file(db_path, db_path.name)

    def _warn_once(self) -> None:
        if self.warned:
            return
        self.warned = True
        log.warning(
            "Google Диск пока не подключён. Файлы пишутся в export/. "
            "Один раз выполни: python run.py --setup-drive"
        )

    def _copy_local(self, path: Path, remote_name: str) -> None:
        assert self.local_dir is not None
        target = self.local_dir / remote_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)

    def _upload(self, path: Path, remote_name: str) -> None:
        from googleapiclient.http import MediaFileUpload

        parent = self.folder_id
        leaf = remote_name
        if remote_name.startswith("md/"):
            parent = self._ensure_md_folder()
            leaf = remote_name.split("/", 1)[1]
        file_id = self.index.get(remote_name, "")
        media = MediaFileUpload(str(path), resumable=True)
        if file_id:
            self.service.files().update(fileId=file_id, media_body=media, supportsAllDrives=True).execute()
            return
        created = self.service.files().create(
            body={"name": leaf, "parents": [parent]},
            media_body=media,
            fields="id",
            supportsAllDrives=True,
        ).execute()
        self.index[remote_name] = created["id"]
        self._save_index()

    def _ensure_md_folder(self) -> str:
        if self.md_folder_id:
            return self.md_folder_id
        found = self.service.files().list(
            q=f"name = 'md' and '{self.folder_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
            fields="files(id)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        files = found.get("files", [])
        if files:
            self.md_folder_id = files[0]["id"]
        else:
            created = self.service.files().create(
                body={
                    "name": "md",
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [self.folder_id],
                },
                fields="id",
                supportsAllDrives=True,
            ).execute()
            self.md_folder_id = created["id"]
        self.index["__md_folder__"] = self.md_folder_id
        self._save_index()
        return self.md_folder_id

    def _load_index(self) -> dict:
        if not self.index_path.exists():
            return {}
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _save_index(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(self.index, ensure_ascii=False, indent=2), encoding="utf-8")


def setup_oauth(root: Path) -> None:
    credentials = root / "credentials.json"
    if not credentials.exists():
        raise SystemExit(
            "Нет credentials.json в папке проекта.\n"
            "1. Открой https://console.cloud.google.com/apis/credentials\n"
            "2. Включи Google Drive API.\n"
            "3. Create credentials → OAuth client ID → Desktop app.\n"
            "4. Скачай JSON и сохрани как credentials.json рядом с run.py.\n"
            "5. Снова запусти: python run.py --setup-drive"
        )
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials), SCOPES)
    creds = flow.run_local_server(port=0)
    (root / "token.json").write_text(creds.to_json(), encoding="utf-8")
    print("Доступ к Google Диску сохранён в token.json. Можно запускать сбор.")
