"""CLI 入口。フローを制御するスクリプト。"""

import argparse
import os
from datetime import datetime
from pathlib import Path

from .config import MainConfig
from .drive_sync import (
    drive_authentication,
    drive_count_descendants,
    drive_delete_folder,
    drive_download_folder_recursive,
    drive_list_children,
    sync_gdx_tree_checkpoint,
)
from .master import (
    apply_gdextraction_master_renames,
    move_gdextraction_to_91_B4_with_master,
    rename_271_shirei_files_to_master,
    write_gdextraction_master_preview_report,
    _pick_master_file,
    _read_csv_master,
)
from .organizer import Config91, Logger, Organizer91
from .utils import ensure_local_dir, p, sanitize_name


def parse_args() -> MainConfig:
    default = MainConfig()
    parser = argparse.ArgumentParser(
        description="91フォルダ整理＆GDX同期スクリプト",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
デフォルト設定:
  GDExtractionルート: {default.gd_root}
  91ルート: {default.target_91_root}
    252ルート: {default.target_252_root}
    92ルート: {default.target_92_root}
    9781ルート: {default.target_9781_root}
  Drive親フォルダ: {default.drive_parent}
  ログディレクトリ: {default.log_dir}

使用例:
  python -m gdx91                           # デフォルト設定で実行
  python -m gdx91 --dry-run                 # テスト実行（ファイル操作なし）
  python -m gdx91 --gd-root /path/to/gd      # GDExtractionパス指定
  python -m gdx91 --target-91-root /path/to/91  # 91フォルダパス指定
        """,
    )
    parser.add_argument(
        "--gd-root",
        metavar="PATH",
        help="GDExtraction のローカルルートフォルダを指定",
    )
    parser.add_argument(
        "--target-91-root",
        metavar="PATH",
        help="91 のルートフォルダを指定",
    )
    parser.add_argument(
        "--target-252-root",
        metavar="PATH",
        help="252 のルートフォルダを指定（指定しない場合は処理しない）",
    )
    parser.add_argument(
        "--target-92-root",
        metavar="PATH",
        help="92 のルートフォルダを指定（指定しない場合は処理しない）",
    )
    parser.add_argument(
        "--target-9781-root",
        metavar="PATH",
        help="9781 のルートフォルダを指定（指定しない場合は処理しない）",
    )
    parser.add_argument(
        "--target-271-root",
        metavar="PATH",
        help="271_修理工事指令書のルートフォルダを指定（指定しない場合はデフォルト値を使用）",
    )
    parser.add_argument(
        "--drive-parent",
        metavar="ID",
        help="Drive 上の同期元親フォルダ ID を指定（'root' も可）",
    )
    parser.add_argument(
        "--log-drive-descendant-counts",
        action="store_true",
        help="Drive フォルダ吸い取り前に配下概算件数をログ出力する",
    )
    parser.add_argument(
        "--no-sync-during-process",
        action="store_true",
        help="処理途中で GDX のフォルダ構成を Drive へ同期しない",
    )
    parser.add_argument(
        "--log-dir",
        metavar="PATH",
        help="ログファイルを出力するディレクトリを指定",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="実際のファイル操作を行わず、処理内容のみ表示する",
    )
    parser.add_argument(
        "--preview-master-renames",
        action="store_true",
        help="GDExtraction の工番マスタ由来リネーム予定だけをログ出力して終了する",
    )
    parser.add_argument(
        "--apply-master-renames-only",
        action="store_true",
        help="GDExtraction の工番マスタ由来リネームだけを実施して終了する",
    )

    args = parser.parse_args()

    cfg = MainConfig(
        gd_root=Path(args.gd_root) if args.gd_root else default.gd_root,
        target_91_root=Path(args.target_91_root) if args.target_91_root else default.target_91_root,
        target_252_root=Path(args.target_252_root) if args.target_252_root else default.target_252_root,
        target_92_root=Path(args.target_92_root) if args.target_92_root else default.target_92_root,
        target_9781_root=Path(args.target_9781_root) if args.target_9781_root else default.target_9781_root,
        target_271_root=Path(args.target_271_root) if args.target_271_root else default.target_271_root,
        drive_parent=args.drive_parent if args.drive_parent else default.drive_parent,
        log_drive_descendant_counts=args.log_drive_descendant_counts,
        sync_gdx_to_drive_during_process=not args.no_sync_during_process,
        log_dir=Path(args.log_dir) if args.log_dir else default.log_dir,
        dry_run=args.dry_run,
        preview_master_renames=args.preview_master_renames,
        apply_master_renames_only=args.apply_master_renames_only,
    )
    return cfg


def main():
    cfg = parse_args()

    if getattr(cfg, "preview_master_renames", False):
        ensure_local_dir(str(cfg.log_dir))
        report_path = cfg.log_dir / f"gdx_master_rename_preview_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        write_gdextraction_master_preview_report(cfg.gd_root, report_path)
        p(f"[PREVIEW] report: {report_path}")
        return

    if getattr(cfg, "apply_master_renames_only", False):
        p("=== GDExtraction 工番マスタ名整合のみ実行 ===")
        service = drive_authentication()
        result = apply_gdextraction_master_renames(
            cfg.gd_root,
            sync_service=service,
            sync_drive_parent_id=cfg.drive_parent,
        )
        p(
            "[APPLY] "
            f"master_count={result['master_count']} "
            f"folder_renamed={result['folder_renamed']} "
            f"file_remaining_plan={result['file_remaining_plan']}"
        )
        return

    p("=== 全工程 開始 ===")
    p(f"GD_ROOT         : {cfg.gd_root}")
    p(f"TARGET_91_ROOT  : {cfg.target_91_root}")
    p(f"TARGET_252_ROOT : {cfg.target_252_root}")
    p(f"TARGET_92_ROOT  : {cfg.target_92_root}")
    p(f"TARGET_9781_ROOT: {cfg.target_9781_root}")
    p(f"DRIVE_PARENT    : {cfg.drive_parent}")

    service = drive_authentication()
    ensure_local_dir(str(cfg.gd_root))
    ensure_local_dir(str(cfg.target_91_root))

    p("=== [1] Drive -> GDExtraction 吸い取り（Drive指定親直下フォルダのみ） ===")
    root_items = drive_list_children(service, cfg.drive_parent)
    drive_folders = [it for it in root_items if it["mimeType"] == "application/vnd.google-apps.folder"]
    p(f"[1] Drive親直下フォルダ数: {len(drive_folders)}")

    for i, f in enumerate(drive_folders, 1):
        folder_name = f["name"]
        folder_id = f["id"]
        local_path = os.path.join(str(cfg.gd_root), sanitize_name(folder_name))

        p(f"[1:{i}/{len(drive_folders)}] DriveFolder: {folder_name} ({folder_id})")
        p(f"  -> local: {local_path}")

        if cfg.log_drive_descendant_counts:
            try:
                folder_count, file_count = drive_count_descendants(service, folder_id)
                p(f"  配下概算: folders={folder_count}, files={file_count}")
            except Exception as e:
                p(f"  [WARN] 配下件数取得失敗: {e}")

        ok = drive_download_folder_recursive(service, folder_id, local_path)
        if ok:
            p(f"  delete on Drive: {folder_name}")
            drive_delete_folder(service, folder_id)
        else:
            p(f"  [WARN] ダウンロード失敗があったためDrive削除をスキップ: {folder_name}")

        if cfg.sync_gdx_to_drive_during_process:
            sync_gdx_tree_checkpoint(service, str(cfg.gd_root), cfg.drive_parent, f"Drive吸い取り後 {folder_name}")

    p("=== [1] 完了 ===")

    p("=== [2] 工番マスタで 252A / 91A をリネームしつつ、GDExtraction -> 91（B4投入） ===")
    move_gdextraction_to_91_B4_with_master(
        gd_root=cfg.gd_root,
        target_91_root=cfg.target_91_root,
        target_252_root=cfg.target_252_root,
        target_92_root=cfg.target_92_root,
        target_9781_root=cfg.target_9781_root,
        delete_empty_src=False,
        sync_service=service,
        sync_drive_parent_id=cfg.drive_parent,
        sync_during_process=cfg.sync_gdx_to_drive_during_process,
    )
    p("=== [2] 完了 ===")

    p("=== [2.5] 271_修理工事指令書 ファイルリネーム（工番_工事名_指令書）===")
    if cfg.target_271_root and cfg.target_271_root.is_dir():
        master_file_271 = _pick_master_file(cfg.gd_root)
        if master_file_271:
            master_271 = _read_csv_master(master_file_271)
            rename_271_shirei_files_to_master(cfg.target_271_root, master_271)
        else:
            p("[WARN] 271リネーム: マスタCSVが見つからないためスキップ")
    else:
        p(f"[SKIP] 271 root not found or not set: {cfg.target_271_root}")
    p("=== [2.5] 完了 ===")

    p("=== [3] 91整理（Organizer91）を実行 ===")
    cfg91 = Config91(target_91_root=cfg.target_91_root, dry_run=cfg.dry_run)
    log_dir = cfg.log_dir
    log_path = log_dir / f"photo_video_91_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log = Logger(log_path)

    try:
        org = Organizer91(cfg91, log)
        org.run()
        log.info("===== 完了 =====")
    finally:
        print(f"\n[LOG] {log.log_path}", flush=True)
        log.close()

    p("=== [3] 完了 ===")

    p("=== [4] GDExtraction のフォルダ構成を Drive へ（空フォルダ）最終同期 ===")
    sync_gdx_tree_checkpoint(service, str(cfg.gd_root), cfg.drive_parent, "最終同期")
    p("=== [4] 完了 ===")

    p("=== 全工程 完了 ===")
