"""CLI および実行時の設定。"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent

_91_ROOT = Path(r"Z:\takachiho\2to9_業務別フォルダ\91_工番別実績写真・動画")


def _default_gd_root() -> Path:
    """写真の手動投入口(MIE = Manual_Input Extraction の投入側)。

    GDX卒業(2026-07-24)後、旧Drive受け皿は「手動投入口」として再利用する。
    現行名は _MIExtraction (2026-07-31改名)。旧名 _manual_input・_GDExtraction
    にもフォールバックする(リネーム前後どちらでも動く)。
    ここに置いた写真フォルダは夜間ランで工番マスタ名に整えられ91へ投入される。
    """
    for name in ("_MIExtraction", "_manual_input", "_GDExtraction"):
        p = _91_ROOT / name
        if p.exists():
            return p
    return _91_ROOT / "_MIExtraction"


@dataclass(frozen=True)
class MainConfig:
    gd_root: Path = field(default_factory=_default_gd_root)
    target_91_root: Path = Path(r"Z:\takachiho\2to9_業務別フォルダ\91_工番別実績写真・動画")
    target_252_root: Optional[Path] = Path(r"Z:\takachiho\2to9_業務別フォルダ\25_リビルト・中古機\252_整備資料")
    target_92_root: Optional[Path] = Path(r"Z:\takachiho\2to9_業務別フォルダ\92_PO LIST")
    target_9781_root: Optional[Path] = Path(r"Z:\takachiho\2to9_業務別フォルダ\97_技術資料\978_CADデータ図庫\9781_工事工番")
    target_271_root: Optional[Path] = Path(r"Z:\takachiho\2to9_業務別フォルダ\27_サービス・出張工事\271_修理工事指令書")
    drive_parent: str = "root"
    # Google Drive 連携([1]吸い取り/[4]同期)を行うか。
    # GDX卒業(2026-07-24〜): 写真取り込みはLINE WORKS botに一本化したため
    # デイリーランでは --no-drive / GDX_NO_DRIVE=1 で無効化して運用する。
    use_drive: bool = True
    log_drive_descendant_counts: bool = False
    sync_gdx_to_drive_during_process: bool = True
    log_dir: Path = PROJECT_ROOT
    dry_run: bool = False
    preview_master_renames: bool = False
    apply_master_renames_only: bool = False
