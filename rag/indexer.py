"""Azure AI Search インデクサー。

処理フロー:
    1. インデックスが無ければ作成（初回のみ）
    2. 91フォルダを走査してメディアファイルのメタデータを収集
    3. Azure AI Search へバッチ upsert
    4. 前回マニフェストと比較して削除されたファイルを検知・除去
    5. マニフェストを更新して保存

差分更新の仕組み:
    id = SHA256(file_path) を使用。
    リネームは「旧 id 削除 + 新 id 追加」として扱われる（自動検知）。
    manifest.json（ローカル保存）に {id: file_path} を記録し、
    前回との差分で孤立した id を AI Search から delete する。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SearchableField,
    SimpleField,
)

from .config import (
    MANIFEST_PATH,
    SEARCH_INDEX_NAME,
    TARGET_91_ROOT,
    TARGET_271_ROOT,
    UPLOAD_BATCH_SIZE,
    ensure_search_credentials,
)
from .describer import load_descriptions, save_descriptions

# ── メディア定義 ───────────────────────────────────────────────────────────────
PHOTO_EXT: frozenset = frozenset({".jpg", ".jpeg", ".png", ".heic", ".heif"})
VIDEO_EXT: frozenset = frozenset({".mp4", ".mov", ".avi", ".mts", ".m2ts"})
PDF_EXT: frozenset = frozenset({".pdf"})
MEDIA_EXT: frozenset = PHOTO_EXT | VIDEO_EXT
JUNK_NAMES: frozenset = frozenset({"Thumbs.db", "desktop.ini", ".DS_Store"})

# ── 工番パターン（master.py と同じロジック） ──────────────────────────────────
_WORKNO_RE = re.compile(r"^([A-Za-z]*\d+[-_]\d{2})")
_WORKNO_NORMALIZE_RE = re.compile(r"^([A-Za-z]*)(\d+)[-_](\d{2})")


def _normalize_workno(code: str) -> Optional[str]:
    s = str(code).strip().lstrip("#")
    m = _WORKNO_NORMALIZE_RE.match(s)
    if not m:
        return None
    prefix = m.group(1).upper()
    digits = m.group(2)
    right = m.group(3)
    if prefix:
        return f"{prefix}{digits}-{right}"
    left = digits.lstrip("0") or "0"
    return f"{left}-{right}"


def _get_workno_from_name(name: str) -> Optional[str]:
    n = str(name).strip().lstrip("#")
    m = _WORKNO_RE.match(n)
    if not m:
        return None
    return _normalize_workno(m.group(1))


# ── A フォルダ名から工番・工事名を取り出す ─────────────────────────────────────
def _parse_a_folder(folder_name: str) -> Tuple[Optional[str], Optional[str]]:
    """(workno, workno_name) を返す。取得できなければ (None, None)。"""
    workno = _get_workno_from_name(folder_name)
    if not workno:
        return None, None
    # 工番部分を除いた残りが工事名
    m = re.match(r"^[A-Za-z]*\d+[-_]\d{2}[_\s]*(.*)", folder_name.strip().lstrip("#"))
    name = m.group(1).strip().lstrip("_- ") if m else ""
    return workno, name or None


# ── B フォルダ（フェーズ）検出 ─────────────────────────────────────────────────
_PHASE_PATTERNS = [("B1", "_B1"), ("B2", "_B2"), ("B3", "_B3"), ("B4", "_B4")]


def _detect_phase(path: Path) -> Optional[str]:
    """パス内のフォルダ名から B1〜B4 を検出する。"""
    for part in path.parts:
        for phase, marker in _PHASE_PATTERNS:
            if marker in part:
                return phase
    return None


# ── ファイル名から撮影日 (YYMMDD) を抽出 ──────────────────────────────────────
_DATE_RE = re.compile(r"_(\d{6})(?:\.\w+)?$")


def _parse_capture_date(filename: str) -> Tuple[Optional[datetime], Optional[str]]:
    """
    ファイル名末尾の _YYMMDD を解析する。

    Returns:
        (datetime（UTC）, 'YYMMDD' 文字列) のタプル。
        解析不能なら (None, None)。
    """
    m = _DATE_RE.search(filename)
    if not m:
        return None, None
    raw = m.group(1)
    yy = int(raw[:2])
    mm = int(raw[2:4])
    dd = int(raw[4:6])
    year = 2000 + yy if yy < 70 else 1900 + yy
    try:
        dt = datetime(year, mm, dd, 0, 0, 0, tzinfo=timezone.utc)
        return dt, raw
    except ValueError:
        return None, None


# ── ドキュメント ID ────────────────────────────────────────────────────────────
def _make_id(file_path: str) -> str:
    """ファイルパスの SHA256 ハッシュ（16進）を ID として返す。"""
    return hashlib.sha256(file_path.encode("utf-8")).hexdigest()


def _rename_key_from_path(path_str: str) -> Optional[Tuple[str, str, str, str]]:
    """リネーム追跡用のキー (工番, フェーズ, 撮影日YYMMDD, 拡張子) を旧パスから作る。

    連番リネームでは (工番フォルダ, Bフェーズ, EXIF由来の撮影日, 拡張子) は不変のため、
    これをキーに旧エントリと新エントリを突き合わせる。キーを作れない場合は None。
    """
    p = Path(path_str)
    ext = p.suffix.lower()
    _dt, raw = _parse_capture_date(p.name)
    if not raw:
        return None
    workno = ""
    for part in p.parts:
        w, _ = _parse_a_folder(part)
        if w:
            workno = w
            break
    if not workno:
        return None
    phase = _detect_phase(p) or ""
    return (workno, phase, raw, ext)


# ── 工事一覧表 CSV 読み込み（納入先・請求先） ──────────────────────────────────────
def _load_orderer_csv(base_dir: Path) -> Dict[str, Dict[str, str]]:
    """発注者一覧表.csv を読み込み {注文者コード: {name, address, tel}} を返す。

    工事一覧表.csv の「工事注文者名称」は略称のことが多いため、
    こちらの「注文者名称１」を正式名称として使う。住所・TELもここから取る。
    ファイルが無い場合は空辞書（従来どおり略称表示にフォールバック）。
    """
    import csv as _csv
    import io as _io

    path = base_dir / "発注者一覧表.csv"
    if not path.exists():
        print("[CSV] 発注者一覧表.csv なし（納入先は略称のまま）", file=sys.stderr)
        return {}

    text = None
    for enc in ("utf-8-sig", "cp932", "utf-8"):
        try:
            text = path.read_text(encoding=enc)
            break
        except Exception:
            text = None
    if not text:
        return {}

    rows = list(_csv.DictReader(_io.StringIO(text)))
    if not rows:
        return {}
    headers = list(rows[0].keys())

    def col(*keys):
        for k in keys:
            for h in headers:
                if h and k in h:
                    return h
        return None

    c_code = col("注文者コード")
    c_name1, c_name2 = col("注文者名称１", "注文者名称1"), col("注文者名称２", "注文者名称2")
    c_zip = col("注文者郵便番号")
    c_ad1, c_ad2 = col("注文者住所１", "注文者住所1"), col("注文者住所２", "注文者住所2")
    c_tel = col("注文者ＴＥＬ", "注文者TEL")
    if not c_code:
        return {}

    out: Dict[str, Dict[str, str]] = {}
    for r in rows:
        code = (r.get(c_code) or "").strip()
        if not code:
            continue
        name = " ".join(
            x for x in [(r.get(c_name1) or "").strip(), (r.get(c_name2) or "").strip()] if x
        )
        zipcode = (r.get(c_zip) or "").strip() if c_zip else ""
        addr = " ".join(
            x for x in [(r.get(c_ad1) or "").strip(), (r.get(c_ad2) or "").strip()] if x
        )
        if zipcode and addr:
            addr = f"〒{zipcode} {addr}"
        elif zipcode:
            addr = f"〒{zipcode}"
        out[code] = {
            "name": name,
            "address": addr,
            "tel": (r.get(c_tel) or "").strip() if c_tel else "",
        }
    print(f"[CSV] 発注者一覧表 読み込み: {len(out)} 件（正式名称・住所）")
    return out


def _load_workno_csv(csv_path: Path) -> Dict[str, Dict[str, str]]:
    """工事一覧表.csv を読み込み {workno: {"client_name": ..., "billing_name": ...}} を返す。
    ※ 納入先は 発注者一覧表.csv と注文者コードで結合し、正式名称・住所を採用する。

    工事注文者名称・工事請求先名称 列がない場合や読み込み失敗時は空辞書を返す（非致命的）。
    """
    import csv as _csv
    import io as _io

    if not csv_path.exists():
        print(f"[CSV] 工事一覧表.csv が見つかりません: {csv_path}", file=sys.stderr)
        return {}

    text: Optional[str] = None
    for enc in ("shift_jis", "cp932", "utf-8-sig", "utf-8"):
        try:
            text = csv_path.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue

    if not text:
        print(f"[CSV] エンコーディング検出失敗: {csv_path}", file=sys.stderr)
        return {}

    try:
        rows = list(_csv.DictReader(_io.StringIO(text)))
    except Exception as e:
        print(f"[CSV] CSV 解析エラー: {e}", file=sys.stderr)
        return {}

    if not rows:
        return {}

    headers = list(rows[0].keys())

    # 工番列（優先順: 「工事番号＋枝番」列を最優先）
    # 260717修正: 基番のみの「工事番号」列(例:00003967)を使うと、枝番違いで納入先が
    # 異なる工番(3967-00=大成, 3967-02=トータス技実 等)が全て同じキーに潰れ、
    # 最後の行で上書きされて誤った納入先が付く問題があった。
    # CSVには枝番付きの「工事番号＋枝番」列(例:00003967-00)が存在するのでこれを最優先で使う。
    def _find_code_col():
        # 「番号」と「枝番」の両方を含む列（＝や+の全角半角に依存しない）
        for h in headers:
            if "番号" in h and "枝番" in h:
                return h
        # フォールバック（従来の挙動）
        for cond in (
            lambda h: h == "工事番号",
            lambda h: "工事番号" in h,
            lambda h: "プロジェクトコード" in h,
            lambda h: "工番" in h,
            lambda h: "コード" in h,
        ):
            m = next((h for h in headers if cond(h)), None)
            if m:
                return m
        return None

    code_col: Optional[str] = _find_code_col()

    # 発注者一覧表（正式名称・住所）。無ければ空で従来どおり。
    orderers = _load_orderer_csv(csv_path.parent)

    # 納入先列（工事注文者名称）
    client_col: Optional[str] = next(
        (h for h in headers if "工事注文者名称" in h or "注文者名称" in h), None
    )

    # 請求先列（工事請求先名称）
    billing_col: Optional[str] = next(
        (h for h in headers if "工事請求先名称" in h or "請求先名称" in h), None
    )

    # 注文者コード列 → 発注者一覧表.csv と結合して正式名称・住所を得る
    client_code_col: Optional[str] = next(
        (h for h in headers if "工事注文者コード" in h or "注文者コード" in h), None
    )

    # 完成日列（工事完成日）→ 値が入っていれば「完成」、空なら「未成」
    kanryo_col: Optional[str] = next(
        (h for h in headers if "工事完成日" in h or h.strip() == "完成日"), None
    )

    # 工事名列（工事名称）→ Botで枝番を選ばせる時に何の工事か見せるために使う
    name_col: Optional[str] = next(
        (h for h in headers if h.strip() == "工事名称"), None
    ) or next(
        (h for h in headers if "工事名称" in h or h.strip() == "工事名"), None
    )

    if not code_col:
        print("[CSV] 工番列が見つかりません", file=sys.stderr)
        return {}

    if not client_col and not billing_col:
        print("[CSV] 工事注文者名称・工事請求先名称 列なし（FMP再エクスポートが必要）", file=sys.stderr)
        return {}

    result: Dict[str, Dict[str, str]] = {}
    for row in rows:
        code_raw = (row.get(code_col) or "").strip()
        # 通常の正規化を試みる（例: "00000001-00" → "1-00"）
        workno = _normalize_workno(code_raw)
        if not workno:
            # サフィックスなし（例: "00000001"）の場合は -00 を補完
            m2 = re.match(r'^([A-Za-z]*)(\d+)$', code_raw)
            if m2:
                prefix = m2.group(1).upper()
                digits = m2.group(2)
                left = digits.lstrip("0") or "0"
                workno = f"{prefix}{left}-00" if prefix else f"{left}-00"
        if not workno:
            continue
        # 完成/未成: 工事完成日が入っていれば「完成」、空なら「未成」。列が無ければ空。
        if kanryo_col:
            kanryo = "完成" if (row.get(kanryo_col) or "").strip() else "未成"
        else:
            kanryo = ""
        # 発注者一覧表で正式名称・住所に置き換える（コードで結合）
        code = (row.get(client_code_col) or "").strip() if client_code_col else ""
        orderer = orderers.get(code) or {}
        formal = orderer.get("name", "")

        result[workno] = {
            # 正式名称があればそちらを採用（工事一覧表の名称は略称のことが多い）
            "client_name": formal or (
                (row.get(client_col) or "").strip() if client_col else ""
            ),
            "client_address": orderer.get("address", ""),
            "client_tel": orderer.get("tel", ""),
            "billing_name": (row.get(billing_col) or "").strip() if billing_col else "",
            "kanryo": kanryo,
            "name": (row.get(name_col) or "").strip() if name_col else "",
        }

    print(
        f"[CSV] 工事マスタ読み込み: {len(result)} 件"
        f" (納入先={'あり' if client_col else 'なし'},"
        f" 請求先={'あり' if billing_col else 'なし'})"
    )
    return result


# ── LWExtraction ファイル名からコメント抽出 ──────────────────────────────────
# lw_blob_sync.py が生成するファイル名: YYYYMMDD_HHMMSS[_部品][_コメント]
_LD_FNAME_RE = re.compile(r"^\d{8}_\d{6}(?:_(.+))?$")


def _parse_ld_comment(stem: str) -> str:
    """LWExtraction ファイル名のステムから 部品_コメント 部分を返す。"""
    m = _LD_FNAME_RE.match(stem)
    if not m or not m.group(1):
        return ""
    return m.group(1).replace("_", " ")


# ── ファイルスキャン ───────────────────────────────────────────────────────────
def scan_media_files(root: Path) -> Iterator[Dict]:
    """
    root 配下のメディアファイルを走査し、ドキュメント辞書を yield する。

    想定構造（通常）:
        root/
          {workno}_{工事名}/          <- A フォルダ
            {workno}_B2着手中写真・動画/
              {workno}_001_250611.jpg

    LWExtraction（LINE WORKS Bot 受信ファイル）:
        root/LWExtraction/
          {workno}/
            YYYYMMDD_HHMMSS_{部品}_{コメント}.mp4
    """
    if not root.is_dir():
        print(f"[WARN] スキャン対象が存在しません: {root}", file=sys.stderr)
        return

    indexed_at = datetime.now(tz=timezone.utc).isoformat()

    for a_folder in sorted(root.iterdir()):
        if not a_folder.is_dir():
            continue

        # ── _LWExtraction サブフォルダの処理 ──────────────────────────────
        if a_folder.name == "_LWExtraction":
            for ld_koban_dir in sorted(a_folder.iterdir()):
                if not ld_koban_dir.is_dir():
                    continue
                workno = _normalize_workno(ld_koban_dir.name)
                if not workno:
                    continue
                for fn in sorted(os.listdir(ld_koban_dir)):
                    file_path = ld_koban_dir / fn
                    if not file_path.is_file():
                        continue
                    if fn in JUNK_NAMES or fn.startswith("~$"):
                        continue
                    ext = file_path.suffix.lower()
                    if ext not in MEDIA_EXT:
                        continue
                    fp_str = str(file_path)
                    capture_dt, capture_raw = _parse_capture_date(fn)
                    # meta.json が隣にあれば読み込む（lw_blob_sync が保存）
                    meta_path = file_path.with_suffix(".json")
                    if meta_path.exists():
                        try:
                            meta = json.loads(meta_path.read_text(encoding="utf-8"))
                            ld_comment = " ".join(filter(None, [
                                meta.get("buhin", ""),
                                meta.get("comment", "") if meta.get("comment", "") not in ("なし", "") else "",
                            ]))
                        except Exception:
                            ld_comment = _parse_ld_comment(file_path.stem)
                    else:
                        ld_comment = _parse_ld_comment(file_path.stem)
                    yield {
                        "id": _make_id(fp_str),
                        "file_path": fp_str,
                        "file_name": fn,
                        "workno": workno,
                        "workno_name": "",
                        "phase": "",
                        "media_type": "photo" if ext in PHOTO_EXT else "video",
                        "capture_date": capture_dt.isoformat() if capture_dt else None,
                        "capture_date_raw": capture_raw or "",
                        "extension": ext,
                        "folder_path": str(ld_koban_dir),
                        "indexed_at": indexed_at,
                        "content_text": ld_comment,  # 部品・コメントを検索可能に
                    }
            continue

        # ── 通常の工番フォルダ処理 ───────────────────────────────────────
        workno, workno_name = _parse_a_folder(a_folder.name)
        if not workno:
            continue  # 工番フォルダ以外はスキップ

        for cur, _dirs, files in os.walk(a_folder):
            cur_path = Path(cur)
            for fn in files:
                if fn in JUNK_NAMES or fn.startswith("~$"):
                    continue
                file_path = cur_path / fn
                ext = file_path.suffix.lower()
                if ext not in MEDIA_EXT:
                    continue

                fp_str = str(file_path)
                capture_dt, capture_raw = _parse_capture_date(fn)

                yield {
                    "id": _make_id(fp_str),
                    "file_path": fp_str,
                    "file_name": fn,
                    "workno": workno,
                    "workno_name": workno_name or "",
                    "phase": _detect_phase(file_path) or "",
                    "media_type": "photo" if ext in PHOTO_EXT else "video",
                    "capture_date": capture_dt.isoformat() if capture_dt else None,
                    "capture_date_raw": capture_raw or "",
                    "extension": ext,
                    "folder_path": str(cur_path),
                    "indexed_at": indexed_at,
                }


# ── 指令書 PDF スキャン（271_修理工事指令書） ───────────────────────────────────
def scan_shirei_files(root_271: Path) -> Iterator[Dict]:
    """
    271_修理工事指令書 配下のPDFファイルを走査し、ドキュメント辞書を yield する。

    想定ファイル名（リネーム後）: {workno}_{工事名}_指令書.pdf
    """
    if not root_271.is_dir():
        print(f"[WARN] 271スキャン対象が存在しません: {root_271}", file=sys.stderr)
        return

    indexed_at = datetime.now(tz=timezone.utc).isoformat()

    for fn in sorted(os.listdir(root_271)):
        file_path = root_271 / fn
        if not file_path.is_file():
            continue
        if fn.startswith("~$") or fn in JUNK_NAMES:
            continue
        ext = file_path.suffix.lower()
        if ext not in PDF_EXT:
            continue

        fp_str = str(file_path)
        stem = file_path.stem  # e.g. "4605-00_第一金属工業_指令書"

        workno = _get_workno_from_name(stem)
        workno_name = ""
        if workno:
            rest = stem[len(workno):].lstrip("_- ")
            if rest.endswith("_指令書"):
                workno_name = rest[: -len("_指令書")]
            else:
                workno_name = rest

        yield {
            "id": _make_id(fp_str),
            "file_path": fp_str,
            "file_name": fn,
            "workno": workno or "",
            "workno_name": workno_name,
            "phase": "",
            "media_type": "shirei",
            "capture_date": None,
            "capture_date_raw": "",
            "extension": ext,
            "folder_path": str(root_271),
            "indexed_at": indexed_at,
            "content_text": "",
        }


# ── マニフェスト（削除検知用） ─────────────────────────────────────────────────
def load_manifest() -> Dict[str, str]:
    """manifest.json から {id: file_path} を読み込む。"""
    if not MANIFEST_PATH.exists():
        return {}
    try:
        with open(MANIFEST_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] マニフェスト読み込み失敗: {e}", file=sys.stderr)
        return {}


def save_manifest(manifest: Dict[str, str]) -> None:
    """マニフェストを manifest.json に保存する。"""
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


# ── インデックス定義 ───────────────────────────────────────────────────────────
def _build_index_definition() -> SearchIndex:
    fields = [
        SimpleField(
            name="id",
            type=SearchFieldDataType.String,
            key=True,
        ),
        SearchableField(
            name="file_path",
            type=SearchFieldDataType.String,
        ),
        SearchableField(
            name="file_name",
            type=SearchFieldDataType.String,
        ),
        SimpleField(
            name="workno",
            type=SearchFieldDataType.String,
            filterable=True,
            facetable=True,
        ),
        SearchableField(
            name="workno_name",
            type=SearchFieldDataType.String,
        ),
        SimpleField(
            name="phase",
            type=SearchFieldDataType.String,
            filterable=True,
            facetable=True,
        ),
        SimpleField(
            name="media_type",
            type=SearchFieldDataType.String,
            filterable=True,
            facetable=True,
        ),
        SimpleField(
            name="capture_date",
            type=SearchFieldDataType.DateTimeOffset,
            filterable=True,
            sortable=True,
        ),
        SimpleField(
            name="capture_date_raw",
            type=SearchFieldDataType.String,
            filterable=True,
        ),
        SimpleField(
            name="extension",
            type=SearchFieldDataType.String,
            filterable=True,
            facetable=True,
        ),
        SearchableField(
            name="folder_path",
            type=SearchFieldDataType.String,
        ),
        SimpleField(
            name="indexed_at",
            type=SearchFieldDataType.DateTimeOffset,
            filterable=True,
            sortable=True,
        ),
        SearchableField(
            name="content_text",
            type=SearchFieldDataType.String,
        ),
        SearchableField(
            name="client_name",
            type=SearchFieldDataType.String,
        ),
        # 納入先の住所・TEL（発注者一覧表.csv 由来）
        SearchableField(
            name="client_address",
            type=SearchFieldDataType.String,
        ),
        SimpleField(
            name="client_tel",
            type=SearchFieldDataType.String,
        ),
        SearchableField(
            name="billing_name",
            type=SearchFieldDataType.String,
        ),
    ]
    return SearchIndex(name=SEARCH_INDEX_NAME, fields=fields)


# ── メイン処理クラス ───────────────────────────────────────────────────────────
class PhotoIndexer:
    def __init__(self):
        endpoint, api_key = ensure_search_credentials()
        cred = AzureKeyCredential(api_key)
        self.index_client = SearchIndexClient(endpoint, cred)
        self.search_client = SearchClient(endpoint, SEARCH_INDEX_NAME, cred)

    # ── インデックス初期化 ─────────────────────────────────────────────────────
    def ensure_index(self) -> None:
        """インデックスを作成または更新する（新フィールドの追加に対応）。"""
        existing = [idx.name for idx in self.index_client.list_indexes()]
        if SEARCH_INDEX_NAME not in existing:
            print(f"[INDEX] インデックスを新規作成: {SEARCH_INDEX_NAME}")
        else:
            print(f"[INDEX] インデックスを更新（新フィールド追加対応）: {SEARCH_INDEX_NAME}")
        self.index_client.create_or_update_index(_build_index_definition())
        print("[INDEX] 完了")

    # ── バッチ upsert ──────────────────────────────────────────────────────────
    def _upload_batch(self, docs: List[Dict]) -> int:
        """docs をまとめて upsert し、成功件数を返す。"""
        if not docs:
            return 0
        results = self.search_client.merge_or_upload_documents(docs)
        ok = sum(1 for r in results if r.succeeded)
        ng = len(docs) - ok
        if ng:
            print(f"[WARN] upsert 失敗: {ng} 件", file=sys.stderr)
        return ok

    # ── バッチ delete ──────────────────────────────────────────────────────────
    def _delete_by_ids(self, ids: List[str]) -> int:
        """id リストのドキュメントを AI Search から削除し、削除件数を返す。"""
        if not ids:
            return 0
        docs = [{"id": doc_id} for doc_id in ids]
        results = self.search_client.delete_documents(docs)
        ok = sum(1 for r in results if r.succeeded)
        return ok

    # ── フルラン ───────────────────────────────────────────────────────────────
    def run(self, root: Optional[Path] = None, root_271: Optional[Path] = None) -> None:
        """
        root 配下を全走査してインデックスを更新する。

        Args:
            root: スキャン対象（91フォルダ）。None の場合は config の TARGET_91_ROOT を使用。
            root_271: 指令書PDFスキャン対象。None の場合は config の TARGET_271_ROOT を使用。
        """
        import itertools

        if root is None:
            root = TARGET_91_ROOT
        if root_271 is None:
            root_271 = TARGET_271_ROOT

        print(f"[START] スキャン開始: {root}")
        print(f"[START] 指令書スキャン: {root_271}")
        self.ensure_index()

        # ── 工事マスタ CSV 読み込み（納入先・請求先） ──────────────────────────
        csv_path = root / "_GDExtraction" / "工事一覧表.csv"
        workno_csv = _load_workno_csv(csv_path)

        # ── 前回マニフェスト読み込み ────────────────────────────────────────
        prev_manifest = load_manifest()
        print(f"[MANIFEST] 前回登録件数: {len(prev_manifest)}")

        # ── 説明文キャッシュ読み込み ────────────────────────────────────────
        descriptions = load_descriptions()
        described_count = sum(1 for v in descriptions.values() if v)
        print(f"[DESCRIPTIONS] 説明文あり: {described_count} 件")

        # ── ファイルスキャン（全件収集） ────────────────────────────────────
        docs_all: List[Dict] = list(
            itertools.chain(scan_media_files(root), scan_shirei_files(root_271))
        )
        new_manifest: Dict[str, str] = {d["id"]: d["file_path"] for d in docs_all}

        # ── リネーム追跡: 孤児エントリの説明文を新エントリへ引き継ぐ ────────
        # (工番, フェーズ, 撮影日, 拡張子) が一致し件数も一致するグループのみ、
        # ファイル名順の対応で引き継ぐ（曖昧な場合は引き継がず再describeに回す）
        migrated = 0
        if docs_all and prev_manifest:
            stale_by_key: Dict[Tuple[str, str, str, str], List[Tuple[str, str]]] = {}
            for old_id, old_path in prev_manifest.items():
                if old_id in new_manifest:
                    continue
                if not descriptions.get(old_id):
                    continue
                key = _rename_key_from_path(old_path)
                if key:
                    stale_by_key.setdefault(key, []).append((Path(old_path).name, old_id))
            pending_by_key: Dict[Tuple[str, str, str, str], List[Tuple[str, str]]] = {}
            for d in docs_all:
                if d["id"] in prev_manifest or descriptions.get(d["id"]):
                    continue
                if d.get("media_type") not in ("photo", "video"):
                    continue
                key = (
                    d.get("workno", ""),
                    d.get("phase", ""),
                    d.get("capture_date_raw", ""),
                    d.get("extension", ""),
                )
                if key[0] and key[2]:
                    pending_by_key.setdefault(key, []).append((d["file_name"], d["id"]))
            for key, olds in stale_by_key.items():
                news = pending_by_key.get(key)
                if not news or len(news) != len(olds):
                    continue
                for (_ofn, oid), (_nfn, nid) in zip(sorted(olds), sorted(news)):
                    descriptions[nid] = descriptions[oid]
                    descriptions.pop(oid, None)
                    migrated += 1
            if migrated:
                save_descriptions(descriptions)
                print(f"[MIGRATE] リネーム追跡: 説明文 {migrated} 件を引き継ぎました（再describe回避）")

        # ── バッチ upsert ──────────────────────────────────────────────────
        batch: List[Dict] = []
        total_scanned = 0
        total_uploaded = 0

        for doc in docs_all:
            # 説明文（AI生成）と LWExtraction コメントを結合して content_text に設定
            ai_desc = descriptions.get(doc["id"], "")
            ld_comment = doc.get("content_text", "")  # scan_media_files が設定済みの場合
            parts = [p for p in [ld_comment, ai_desc] if p]
            doc["content_text"] = " ".join(parts)
            # 工事マスタ CSV から納入先・請求先を補完
            csv_info = workno_csv.get(doc.get("workno", ""), {})
            doc["client_name"] = csv_info.get("client_name", "")
            doc["client_address"] = csv_info.get("client_address", "")
            doc["client_tel"] = csv_info.get("client_tel", "")
            doc["billing_name"] = csv_info.get("billing_name", "")
            new_manifest[doc["id"]] = doc["file_path"]
            batch.append(doc)
            total_scanned += 1

            if len(batch) >= UPLOAD_BATCH_SIZE:
                total_uploaded += self._upload_batch(batch)
                print(f"[UPLOAD] {total_uploaded}/{total_scanned} 件 upsert 完了")
                batch = []

        # 残りをフラッシュ
        if batch:
            total_uploaded += self._upload_batch(batch)

        print(f"[UPLOAD] 完了: スキャン={total_scanned}, upsert={total_uploaded}")

        # ── 削除検知（前回にあって今回ない = ファイルが消えた or リネームされた） ──
        stale_ids = [
            doc_id
            for doc_id in prev_manifest
            if doc_id not in new_manifest
        ]
        # 大量消失保護: スキャン件数が前回の半分未満なら削除・マニフェスト更新を行わない。
        # (260713: Z:は生きていたが91スキャンが0件・PDFのみ1,997件となり、
        #  「空スキャン保護」をすり抜けて写真・動画13,600件がインデックスから全削除された事故の再発防止)
        mass_shrink = bool(
            prev_manifest and len(new_manifest) < len(prev_manifest) * 0.5
        )
        if not docs_all:
            # Z:未接続などの空スキャン時はインデックスを保護（マニフェスト保護と同じ基準）
            print(
                f"[DELETE] スキャン結果が0件のため削除をスキップしました（インデックス {len(prev_manifest)} 件を保護）",
                file=sys.stderr,
            )
        elif mass_shrink:
            print(
                f"[DELETE] スキャン件数が前回の半分未満のため削除をスキップしました"
                f"（今回 {len(new_manifest)} 件 / 前回 {len(prev_manifest)} 件。大量消失保護）",
                file=sys.stderr,
            )
        elif stale_ids:
            deleted = self._delete_by_ids(stale_ids)
            print(f"[DELETE] 孤立ドキュメント削除: {deleted} 件")
            for doc_id in stale_ids[:20]:
                print(f"         削除: {prev_manifest[doc_id]}")
            if len(stale_ids) > 20:
                print(f"         ...他 {len(stale_ids) - 20} 件（全リストはmanifest差分で確認可）")
        else:
            print("[DELETE] 削除対象なし")

        # ── マニフェスト更新 ────────────────────────────────────────────────
        # 空スキャン・大量消失時は既存を保護
        if new_manifest and not mass_shrink:
            save_manifest(new_manifest)
            print(f"[MANIFEST] 更新完了: {len(new_manifest)} 件")
        elif mass_shrink:
            print(f"[MANIFEST] 大量消失保護のため更新をスキップしました（既存 {len(prev_manifest)} 件を保持）", file=sys.stderr)
        else:
            print(f"[MANIFEST] スキャン結果が0件のため更新をスキップしました（既存 {len(prev_manifest)} 件を保持）", file=sys.stderr)
        print("[DONE] インデックス更新完了")
