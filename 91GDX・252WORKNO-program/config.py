"""CLI および実行時の設定。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class MainConfig:
    gd_root: Path = Path(r"Z:\takachiho\2to9_業務別フォルダ\91_工番別実績写真・動画\GDExtraction")
    target_91_root: Path = Path(r"Z:\takachiho\2to9_業務別フォルダ\91_工番別実績写真・動画")
    target_252_root: Optional[Path] = Path(r"Z:\takachiho\2to9_業務別フォルダ\25_リビルト・中古機\252_整備資料")
    target_92_root: Optional[Path] = Path(r"Z:\takachiho\2to9_業務別フォルダ\92_PO_LIST")
    target_9781_root: Optional[Path] = Path(r"Z:\takachiho\2to9_業務別フォルダ\97_技術資料\978_CADデータ図庫\9781_工事工番")
    drive_parent: str = "root"
    log_drive_descendant_counts: bool = False
    sync_gdx_to_drive_during_process: bool = True
    log_dir: Path = PROJECT_ROOT
    dry_run: bool = False
    preview_master_renames: bool = False
    apply_master_renames_only: bool = False
