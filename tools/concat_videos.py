from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mts", ".m2ts"}
DEFAULT_PROBESIZE = "50M"
DEFAULT_ANALYZEDURATION = "50M"
DEFAULT_VIDEO_CODEC = "libx264"
DEFAULT_AUDIO_CODEC = "aac"
DEFAULT_CRF = "18"
DEFAULT_PRESET = "medium"
DEFAULT_AUDIO_BITRATE = "192k"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="フォルダ内の動画を自然順で結合します。まず copy を試し、失敗時は再エンコードへ切り替えます。"
    )
    parser.add_argument("input_dir", type=Path, help="結合対象動画が入っているフォルダ")
    parser.add_argument("output_file", type=Path, help="結合後の出力ファイル")
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="サブフォルダ内の動画も対象にします",
    )
    parser.add_argument(
        "--probesize",
        default=DEFAULT_PROBESIZE,
        help="ffmpeg の probesize。既定値: 50M",
    )
    parser.add_argument(
        "--analyzeduration",
        default=DEFAULT_ANALYZEDURATION,
        help="ffmpeg の analyzeduration。既定値: 50M",
    )
    parser.add_argument(
        "--crf",
        default=DEFAULT_CRF,
        help="再エンコード時の CRF。既定値: 18",
    )
    parser.add_argument(
        "--preset",
        default=DEFAULT_PRESET,
        help="再エンコード時の preset。既定値: medium",
    )
    parser.add_argument(
        "--audio-bitrate",
        default=DEFAULT_AUDIO_BITRATE,
        help="再エンコード時の音声ビットレート。既定値: 192k",
    )
    return parser.parse_args()


def natural_key(text: str) -> list[object]:
    parts = re.split(r"(\d+)", text)
    return [int(part) if part.isdigit() else part.lower() for part in parts]


def ensure_ffmpeg() -> str:
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except FileNotFoundError:
        raise
    except subprocess.CalledProcessError:
        pass
    return "ffmpeg"


def collect_videos(input_dir: Path, output_file: Path, recursive: bool) -> list[Path]:
    iterator = input_dir.rglob("*") if recursive else input_dir.iterdir()
    files = [path for path in iterator if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS]

    try:
        resolved_output = output_file.resolve()
        files = [path for path in files if path.resolve() != resolved_output]
    except OSError:
        pass

    files.sort(key=lambda path: natural_key(path.name))
    return files


def write_concat_list_temp(input_dir: Path, files: list[Path]) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    list_file = input_dir / f"._concat_list_{stamp}.txt"
    with list_file.open("w", encoding="utf-8", newline="\n") as handle:
        for path in files:
            escaped = str(path).replace("'", r"\'")
            handle.write(f"file '{escaped}'\n")
    return list_file


def run_ffmpeg(command: list[str], log_file: Path) -> int:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("=== CMD ===\n")
        handle.write(" ".join(command) + "\n\n")
        handle.write("=== STDERR ===\n")
        process = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=handle, text=True)
    return process.returncode


def cmd_concat_copy(
    ffmpeg: str,
    list_file: Path,
    output_file: Path,
    analyzeduration: str,
    probesize: str,
) -> list[str]:
    return [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-analyzeduration",
        analyzeduration,
        "-probesize",
        probesize,
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        "copy",
        "-c:a",
        "copy",
        "-fflags",
        "+genpts",
        "-avoid_negative_ts",
        "make_zero",
        str(output_file),
    ]


def cmd_concat_reencode(
    ffmpeg: str,
    list_file: Path,
    output_file: Path,
    analyzeduration: str,
    probesize: str,
    preset: str,
    crf: str,
    audio_bitrate: str,
) -> list[str]:
    return [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-analyzeduration",
        analyzeduration,
        "-probesize",
        probesize,
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        DEFAULT_VIDEO_CODEC,
        "-preset",
        preset,
        "-crf",
        crf,
        "-c:a",
        DEFAULT_AUDIO_CODEC,
        "-b:a",
        audio_bitrate,
        "-movflags",
        "+faststart",
        str(output_file),
    ]


def main() -> int:
    args = parse_args()
    input_dir = args.input_dir
    output_file = args.output_file

    if not input_dir.exists() or not input_dir.is_dir():
        print(f"[ERROR] 入力フォルダが存在しません: {input_dir}")
        return 1

    output_file.parent.mkdir(parents=True, exist_ok=True)

    files = collect_videos(input_dir, output_file, args.recursive)
    if not files:
        print(f"[ERROR] 動画が見つかりません: {input_dir}")
        return 1

    print("=== 結合順 ===")
    for path in files:
        print(path)

    try:
        ffmpeg = ensure_ffmpeg()
    except FileNotFoundError:
        print("[ERROR] ffmpeg が見つかりません。ffmpeg -version が通る状態にしてください。")
        return 1

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_copy = output_file.parent / f"ffmpeg_{stamp}_copy.log"
    log_reencode = output_file.parent / f"ffmpeg_{stamp}_reencode.log"
    list_file: Path | None = None

    try:
        list_file = write_concat_list_temp(input_dir, files)

        copy_command = cmd_concat_copy(
            ffmpeg,
            list_file,
            output_file,
            args.analyzeduration,
            args.probesize,
        )
        print("\n=== まずは -c copy で結合 ===")
        print(" ".join(copy_command))
        print(f"ログ: {log_copy}")
        copy_result = run_ffmpeg(copy_command, log_copy)
        if copy_result == 0:
            print(f"\n[DONE] 出力: {output_file}")
            print(f"[DONE] ログ: {log_copy}")
            return 0

        reencode_command = cmd_concat_reencode(
            ffmpeg,
            list_file,
            output_file,
            args.analyzeduration,
            args.probesize,
            args.preset,
            args.crf,
            args.audio_bitrate,
        )
        print("\n=== copy が失敗したので再エンコードで結合 ===")
        print(" ".join(reencode_command))
        print(f"ログ: {log_reencode}")
        reencode_result = run_ffmpeg(reencode_command, log_reencode)
        if reencode_result == 0:
            print(f"\n[DONE] 出力: {output_file}")
            print(f"[DONE] ログ(copy失敗): {log_copy}")
            print(f"[DONE] ログ(再エンコード): {log_reencode}")
            return 0

        print("\n[ERROR] 再エンコードでも失敗しました。ログを確認してください。")
        print(f"  copyログ: {log_copy}")
        print(f"  reencodeログ: {log_reencode}")
        return 2
    finally:
        if list_file and list_file.exists():
            try:
                list_file.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())