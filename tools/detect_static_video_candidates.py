from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mts", ".m2ts"}


@dataclass
class VideoAnalysis:
    path: Path
    duration_sec: float
    sample_count: int
    diff_count: int
    avg_diff: float
    max_diff: float
    still_ratio: float
    max_still_span_sec: float
    candidate: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "動画から低解像度フレームを一定間隔で取り出し、"
            "同じ画角が長く続く候補を CSV で出力します。"
        )
    )
    parser.add_argument("input_path", type=Path, help="判定対象の動画ファイルまたはフォルダ")
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="フォルダ指定時にサブフォルダも対象にします",
    )
    parser.add_argument(
        "--min-duration",
        type=float,
        default=15.0,
        help="候補判定対象にする最小動画秒数。既定値: 15",
    )
    parser.add_argument(
        "--sample-fps",
        type=float,
        default=1.0,
        help="フレーム抽出頻度。既定値: 1.0 fps",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=64,
        help="比較用フレームの横幅。既定値: 64",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=36,
        help="比較用フレームの縦幅。既定値: 36",
    )
    parser.add_argument(
        "--diff-threshold",
        type=float,
        default=3.0,
        help="連続フレーム差分のしきい値。小さいほど厳しめ。既定値: 3.0",
    )
    parser.add_argument(
        "--still-ratio-threshold",
        type=float,
        default=0.8,
        help="差分が小さい区間の割合がこの値以上なら候補。既定値: 0.8",
    )
    parser.add_argument(
        "--min-still-seconds",
        type=float,
        default=20.0,
        help="ほぼ同じ画角が連続して続いたとみなす最小秒数。既定値: 20",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=None,
        help="結果 CSV の出力先。未指定なら logs 配下へ自動生成します",
    )
    return parser.parse_args()


def ensure_command(name: str) -> None:
    try:
        subprocess.run(
            [name, "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"{name} が見つかりません。PATH を確認してください。") from exc
    except subprocess.CalledProcessError:
        return


def collect_videos(input_path: Path, recursive: bool) -> list[Path]:
    if input_path.is_file():
        return [input_path] if input_path.suffix.lower() in VIDEO_EXTENSIONS else []

    if not input_path.is_dir():
        return []

    iterator = input_path.rglob("*") if recursive else input_path.iterdir()
    files = [path for path in iterator if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS]
    files.sort(key=lambda path: str(path).lower())
    return files


def probe_duration(video_path: Path) -> float:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(video_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffprobe duration failed")

    payload = json.loads(result.stdout or "{}")
    duration = payload.get("format", {}).get("duration")
    return float(duration) if duration is not None else 0.0


def extract_sample_frames(video_path: Path, sample_fps: float, width: int, height: int) -> list[bytes]:
    frame_size = width * height
    vf = f"fps={sample_fps},scale={width}:{height}:flags=lanczos,format=gray"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-vf",
        vf,
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "pipe:1",
    ]
    result = subprocess.run(command, capture_output=True)
    if result.returncode != 0:
        message = (result.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(message or "ffmpeg frame extraction failed")

    payload = result.stdout or b""
    if not payload:
        return []

    frame_count = len(payload) // frame_size
    return [payload[index * frame_size:(index + 1) * frame_size] for index in range(frame_count)]


def mean_abs_diff(frame_a: bytes, frame_b: bytes) -> float:
    total = 0
    for left, right in zip(frame_a, frame_b):
        total += abs(left - right)
    return total / len(frame_a)


def longest_still_span_sec(diffs: list[float], diff_threshold: float, sample_fps: float) -> float:
    current_run = 0
    longest_run = 0
    for diff in diffs:
        if diff <= diff_threshold:
            current_run += 1
            if current_run > longest_run:
                longest_run = current_run
        else:
            current_run = 0

    if sample_fps <= 0:
        return 0.0
    return longest_run / sample_fps


def analyze_video(
    video_path: Path,
    min_duration: float,
    sample_fps: float,
    width: int,
    height: int,
    diff_threshold: float,
    still_ratio_threshold: float,
    min_still_seconds: float,
) -> VideoAnalysis:
    duration_sec = probe_duration(video_path)
    frames = extract_sample_frames(video_path, sample_fps, width, height)

    if len(frames) < 2:
        return VideoAnalysis(
            path=video_path,
            duration_sec=duration_sec,
            sample_count=len(frames),
            diff_count=0,
            avg_diff=0.0,
            max_diff=0.0,
            still_ratio=0.0,
            max_still_span_sec=0.0,
            candidate=False,
        )

    diffs = [mean_abs_diff(prev_frame, next_frame) for prev_frame, next_frame in zip(frames, frames[1:])]
    still_count = sum(1 for diff in diffs if diff <= diff_threshold)
    still_ratio = still_count / len(diffs)
    avg_diff = sum(diffs) / len(diffs)
    max_diff = max(diffs)
    max_still_span = longest_still_span_sec(diffs, diff_threshold, sample_fps)
    candidate = (
        duration_sec >= min_duration
        and still_ratio >= still_ratio_threshold
        and max_still_span >= min_still_seconds
    )

    return VideoAnalysis(
        path=video_path,
        duration_sec=duration_sec,
        sample_count=len(frames),
        diff_count=len(diffs),
        avg_diff=avg_diff,
        max_diff=max_diff,
        still_ratio=still_ratio,
        max_still_span_sec=max_still_span,
        candidate=candidate,
    )


def default_output_csv() -> Path:
    log_dir = Path(__file__).resolve().parents[1] / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return log_dir / f"static_video_candidates_{stamp}.csv"


def write_csv(output_csv: Path, rows: list[VideoAnalysis]) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "candidate",
            "path",
            "duration_sec",
            "sample_count",
            "diff_count",
            "avg_diff",
            "max_diff",
            "still_ratio",
            "max_still_span_sec",
        ])
        for row in rows:
            writer.writerow([
                "YES" if row.candidate else "NO",
                str(row.path),
                f"{row.duration_sec:.2f}",
                row.sample_count,
                row.diff_count,
                f"{row.avg_diff:.4f}",
                f"{row.max_diff:.4f}",
                f"{row.still_ratio:.4f}",
                f"{row.max_still_span_sec:.2f}",
            ])


def main() -> int:
    args = parse_args()
    ensure_command("ffmpeg")
    ensure_command("ffprobe")

    videos = collect_videos(args.input_path, args.recursive)
    if not videos:
        print(f"[ERROR] 動画が見つかりません: {args.input_path}")
        return 1

    rows: list[VideoAnalysis] = []
    for index, video_path in enumerate(videos, 1):
        print(f"[{index}/{len(videos)}] 判定中: {video_path}")
        try:
            row = analyze_video(
                video_path=video_path,
                min_duration=args.min_duration,
                sample_fps=args.sample_fps,
                width=args.width,
                height=args.height,
                diff_threshold=args.diff_threshold,
                still_ratio_threshold=args.still_ratio_threshold,
                min_still_seconds=args.min_still_seconds,
            )
        except Exception as exc:
            print(f"  [WARN] 判定失敗: {exc}")
            continue

        rows.append(row)
        print(
            "  "
            f"duration={row.duration_sec:.1f}s "
            f"avg_diff={row.avg_diff:.3f} "
            f"still_ratio={row.still_ratio:.3f} "
            f"max_still={row.max_still_span_sec:.1f}s "
            f"candidate={'YES' if row.candidate else 'NO'}"
        )

    if not rows:
        print("[ERROR] 判定できた動画がありませんでした。")
        return 2

    output_csv = args.output_csv or default_output_csv()
    rows.sort(key=lambda row: (not row.candidate, row.avg_diff, -row.still_ratio, str(row.path).lower()))
    write_csv(output_csv, rows)

    candidate_count = sum(1 for row in rows if row.candidate)
    print("")
    print(f"[DONE] 結果CSV: {output_csv}")
    print(f"[DONE] 判定動画数: {len(rows)} / 候補数: {candidate_count}")
    print("[INFO] 候補は candidate=YES として CSV 先頭側に並べています。")
    return 0


if __name__ == "__main__":
    sys.exit(main())