"""Google Drive との同期／ダウンロード処理。"""

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict

try:
    from google.auth.transport.requests import Request
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaIoBaseDownload
except ImportError as e:
    raise ImportError(
        "Missing Google Drive dependencies. Install requirements with 'pip install -r requirements.txt'."
    ) from e

from .utils import (
    ensure_local_dir,
    escape_gdrive_query_value,
    now_ts,
    p,
    sanitize_name,
)

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"

EXPORT_MIME_TYPES = {
    "application/vnd.google-apps.document":
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    "application/vnd.google-apps.spreadsheet":
        ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "application/vnd.google-apps.presentation":
        ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
    "application/vnd.google-apps.drawing":
        ("application/pdf", ".pdf"),
}

MODULE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = MODULE_DIR.parent


def _runtime_root() -> Path | None:
    value = os.environ.get("GDX_RUNTIME_ROOT")
    if not value:
        return None
    try:
        return Path(value)
    except Exception:
        return None


def _resolve_auth_path(filename: str, *, prefer_existing: bool = True, prefer_runtime: bool = False) -> Path:
    runtime_root = _runtime_root()
    if prefer_runtime and runtime_root:
        return runtime_root / filename

    candidates = [
        runtime_root / filename if runtime_root else None,
        Path.cwd() / filename,
        PROJECT_DIR / filename,
        MODULE_DIR / filename,
    ]
    if prefer_existing:
        for candidate in candidates:
            if candidate is None:
                continue
            if candidate.exists():
                return candidate
    if runtime_root:
        return runtime_root / filename
    return PROJECT_DIR / filename


def with_backoff(func, *, max_retry: int = 6, base_sleep: float = 1.0):
    for attempt in range(max_retry):
        try:
            return func()
        except HttpError as e:
            status = getattr(e.resp, "status", None)
            if status in (403, 429, 500, 503):
                sleep = base_sleep * (2 ** attempt)
                p(f"一時エラー({status}) リトライ {attempt + 1}/{max_retry} ... {sleep:.1f}s")
                time.sleep(sleep)
                continue
            raise
    raise RuntimeError("リトライ上限に達しました")


def drive_authentication() -> object:
    """Google Drive 認証を行い、service オブジェクトを返す。"""
    p("[AUTH] Google Drive 認証開始")
    # テスト／ローカル実行用に Drive を無効化するフラグをサポート
    skip = os.environ.get("GDX_SKIP_DRIVE")
    if skip and skip.lower() in ("1", "true", "yes"):
        p("[AUTH] 環境変数で Drive をスキップします (GDX_SKIP_DRIVE=1)")

        class _FakeRequest:
            def __init__(self, result=None):
                self._result = result or {}

            def execute(self):
                return self._result

        class _FilesResource:
            def list(self, **kwargs):
                return _FakeRequest({"files": []})

            def create(self, **kwargs):
                return _FakeRequest({"id": "fake-folder-id"})

            def delete(self, **kwargs):
                return _FakeRequest({})

            def get_media(self, **kwargs):
                return _FakeRequest()

            def export_media(self, **kwargs):
                return _FakeRequest()

        class _FakeService:
            def files(self):
                return _FilesResource()

        return _FakeService()
    creds = None
    token_path = _resolve_auth_path("token.json", prefer_runtime=True)
    credentials_path = _resolve_auth_path("credentials.json")

    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), DRIVE_SCOPES)
            p(f"[AUTH] token.json 読み込み成功: {token_path}")
        except Exception:
            creds = None
            p(f"[AUTH] token.json 読み込み失敗、再認証へ: {token_path}")

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                p("[AUTH] token refresh 実行")
                creds.refresh(Request())
                p("[AUTH] token refresh 成功")
            except RefreshError as e:
                msg = str(e)
                if "invalid_grant" in msg or "expired or revoked" in msg:
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_path = token_path.with_name(f"token.revoked.{ts}.json")
                    try:
                        os.replace(str(token_path), str(backup_path))
                        p(f"[AUTH] token.json が無効化されていたため退避: {backup_path}")
                    except Exception:
                        pass
                    creds = None
                else:
                    raise

        if not creds or not creds.valid:
            p("[AUTH] ブラウザ認証を開始します")
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), DRIVE_SCOPES)
            creds = flow.run_local_server(
                port=0,
                access_type="offline",
                prompt="consent",
            )
            p("[AUTH] ブラウザ認証完了")

        token_path.parent.mkdir(parents=True, exist_ok=True)
        with open(token_path, "w", encoding="utf-8") as token:
            token.write(creds.to_json())
        p(f"[AUTH] token.json 保存完了: {token_path}")

    service = build("drive", "v3", credentials=creds)
    p("[AUTH] Google Drive service 作成完了")
    return service


def drive_list_children(service, parent_id: str):
    items = []
    page_token = None
    query = f"'{escape_gdrive_query_value(parent_id)}' in parents and trashed = false"
    while True:
        res = with_backoff(lambda: service.files().list(
            q=query,
            pageSize=1000,
            fields="nextPageToken, files(id, name, mimeType)",
            pageToken=page_token,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        ).execute())
        batch = res.get("files", [])
        items.extend(batch)
        page_token = res.get("nextPageToken")
        if not page_token:
            break
    return items


def drive_download_file_overwrite(service, file_id: str, file_name: str, mime_type: str, local_dir: str) -> bool:
    """Drive -> local ダウンロード（同名は上書き）。"""
    file_name = sanitize_name(file_name)

    try:
        if mime_type in EXPORT_MIME_TYPES:
            export_mime, extension = EXPORT_MIME_TYPES[mime_type]
            if not file_name.lower().endswith(extension):
                file_name += extension
            request = service.files().export_media(fileId=file_id, mimeType=export_mime)
        elif mime_type.startswith("application/vnd.google-apps"):
            p(f"スキップ（非対応形式）: {file_name} ({mime_type})")
            return False
        else:
            request = service.files().get_media(fileId=file_id)

        ensure_local_dir(local_dir)
        output_path = os.path.join(local_dir, file_name)

        with open(output_path, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            last_percent = -1

            while not done:
                status, done = with_backoff(downloader.next_chunk)
                if status:
                    percent = int(status.progress() * 100)
                    if percent != last_percent:
                        p(f"  DL {os.path.basename(output_path)} ... {percent}%")
                        last_percent = percent

        p(f"✓ 保存: {output_path}")
        return True

    except HttpError as e:
        p(f"エラー ({file_name}): {e}")
        return False
    except Exception as e:
        p(f"エラー ({file_name}): {e}")
        return False


def drive_count_descendants(service, drive_folder_id: str) -> tuple[int, int]:
    """フォルダ配下の概算件数を返す (folders, files)。"""
    folder_count = 0
    file_count = 0
    stack = [drive_folder_id]

    while stack:
        current = stack.pop()
        children = drive_list_children(service, current)
        for it in children:
            if it["mimeType"] == DRIVE_FOLDER_MIME:
                folder_count += 1
                stack.append(it["id"])
            else:
                file_count += 1
    return folder_count, file_count


def drive_download_folder_recursive(service, drive_folder_id: str, local_target_dir: str, depth: int = 0) -> bool:
    """フォルダ配下を再帰ダウンロード。1件でも失敗したら False。"""
    ensure_local_dir(local_target_dir)
    ok = True

    items = drive_list_children(service, drive_folder_id)
    total = len(items)
    p(f"{'  ' * depth}[SCAN] {local_target_dir} : 子要素 {total}件")

    for idx, it in enumerate(items, 1):
        name = it["name"]
        fid = it["id"]
        mt = it["mimeType"]

        p(f"{'  ' * depth}[ITEM {idx}/{total}] {name} ({mt})")

        if mt == DRIVE_FOLDER_MIME:
            sub_dir = os.path.join(local_target_dir, sanitize_name(name))
            if not drive_download_folder_recursive(service, fid, sub_dir, depth + 1):
                ok = False
        else:
            if not drive_download_file_overwrite(service, fid, name, mt, local_target_dir):
                ok = False
    return ok


def drive_delete_folder(service, folder_id: str):
    """Drive上のフォルダを完全削除する。"""
    with_backoff(lambda: service.files().delete(
        fileId=folder_id,
        supportsAllDrives=True
    ).execute())


def drive_find_or_create_folder(service, parent_id: str, folder_name: str) -> str:
    safe_name = escape_gdrive_query_value(folder_name)
    query = (
        f"'{escape_gdrive_query_value(parent_id)}' in parents and trashed = false "
        f"and mimeType = '{DRIVE_FOLDER_MIME}' and name = '{safe_name}'"
    )

    res = with_backoff(lambda: service.files().list(
        q=query,
        pageSize=10,
        fields="files(id, name, mimeType)",
        includeItemsFromAllDrives=True,
        supportsAllDrives=True,
    ).execute())
    files = res.get("files", [])
    if files:
        return files[0]["id"]

    meta = {"name": folder_name, "mimeType": DRIVE_FOLDER_MIME, "parents": [parent_id]}
    created = with_backoff(lambda: service.files().create(
        body=meta, fields="id", supportsAllDrives=True
    ).execute())
    p(f"＋ Driveフォルダ作成: {folder_name}")
    return created["id"]


def drive_delete_named_child_folders(service, parent_id: str, folder_name: str) -> int:
    safe_name = escape_gdrive_query_value(folder_name)
    query = (
        f"'{escape_gdrive_query_value(parent_id)}' in parents and trashed = false "
        f"and mimeType = '{DRIVE_FOLDER_MIME}' and name = '{safe_name}'"
    )

    res = with_backoff(lambda: service.files().list(
        q=query,
        pageSize=100,
        fields="files(id, name)",
        includeItemsFromAllDrives=True,
        supportsAllDrives=True,
    ).execute())

    deleted = 0
    for item in res.get("files", []):
        with_backoff(lambda item_id=item["id"]: service.files().delete(
            fileId=item_id,
            supportsAllDrives=True,
        ).execute())
        deleted += 1
        p(f"- Driveフォルダ削除: {item['name']}")
    return deleted


def local_folder_tree(base_dir: str) -> Dict:
    root_name = os.path.basename(os.path.normpath(base_dir))

    def _walk(path: str, name: str) -> Dict:
        node = {"name": name, "children": []}
        try:
            entries = os.listdir(path)
        except Exception:
            entries = []
        folders = [e for e in entries if os.path.isdir(os.path.join(path, e))]
        for fname in sorted(folders, key=lambda s: s.lower()):
            node["children"].append(_walk(os.path.join(path, fname), fname))
        return node

    return _walk(base_dir, root_name)


def drive_recreate_folder_tree_only(service, parent_id: str, tree: Dict):
    name = tree["name"]
    new_id = drive_find_or_create_folder(service, parent_id, name)
    for child in tree.get("children", []):
        drive_recreate_folder_tree_only(service, new_id, child)


def drive_sync_local_children_tree(service, local_base_dir: str, drive_parent_id: str):
    """local_base_dir 直下の各フォルダを Drive 直下に同期（空フォルダ構成）。"""
    ensure_local_dir(local_base_dir)
    local_children = [
        d for d in os.listdir(local_base_dir)
        if os.path.isdir(os.path.join(local_base_dir, d))
    ]
    p(f"[SYNC] ローカル子フォルダ数: {len(local_children)}")

    for idx, folder in enumerate(sorted(local_children, key=lambda s: s.lower()), 1):
        tree = local_folder_tree(os.path.join(local_base_dir, folder))
        p(f"[SYNC {idx}/{len(local_children)}] Driveへ反映: {folder}")
        drive_recreate_folder_tree_only(service, drive_parent_id, tree)


def sync_gdx_tree_checkpoint(service, gd_root: str, drive_parent_id: str, reason: str = ""):
    if not service or not drive_parent_id:
        return
    p(f"=== [SYNC] GDExtraction フォルダ構成を Drive へ同期{' : ' + reason if reason else ''} ===")
    drive_sync_local_children_tree(service, gd_root, drive_parent_id)
    p("=== [SYNC] 完了 ===")
