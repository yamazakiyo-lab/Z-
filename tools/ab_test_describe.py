"""V2/V3 describe A/Bテスト — 同一画像を両設定で生成して比較する。

使い方(デスクトップ、.env が読める場所で):
  python tools/ab_test_describe.py --count 20            # manifestからランダム20枚
  python tools/ab_test_describe.py --workno 3611-02      # 特定工番から抽出

出力: ab_test_describe_YYYYMMDD_HHMMSS.md(V2/V3の説明文を並べた比較表)

コスト目安: 20枚 × (V2約0.9円 + V3約0.1円) = 約20円
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    parser = argparse.ArgumentParser(description="V2/V3 describe A/Bテスト")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--workno", type=str, default="")
    args = parser.parse_args()

    from rag.indexer import load_manifest
    from rag.describer import describe_image, VISION_SUPPORTED_EXT
    from rag.config import OPENAI_GPT4O_DEPLOYMENT
    import os

    mini = os.getenv("OPENAI_MINI_DEPLOYMENT", "gpt-4o-mini")

    manifest = load_manifest()
    candidates = [
        (doc_id, fp) for doc_id, fp in manifest.items()
        if Path(fp).suffix.lower() in VISION_SUPPORTED_EXT
        and (not args.workno or f"\\{args.workno}" in fp or f"/{args.workno}" in fp)
        and Path(fp).exists()
    ]
    if not candidates:
        print("[ERROR] 対象画像が見つかりません", file=sys.stderr)
        sys.exit(1)
    random.shuffle(candidates)
    samples = candidates[: args.count]
    print(f"[AB] 対象 {len(samples)} 枚(候補 {len(candidates)} 枚から抽出)")

    rows = []
    for i, (doc_id, fp) in enumerate(samples, 1):
        p = Path(fp)
        print(f"[{i}/{len(samples)}] {p.name}")
        t0 = time.time()
        v2 = describe_image(p, deployment=OPENAI_GPT4O_DEPLOYMENT, detail="high", max_tokens=300)
        t_v2 = time.time() - t0
        t0 = time.time()
        v3 = describe_image(p, deployment=mini, detail="low", max_tokens=150)
        t_v3 = time.time() - t0
        rows.append((p.name, v2, t_v2, v3, t_v3))
        print(f"    V2({t_v2:.1f}s): {v2[:60]}")
        print(f"    V3({t_v3:.1f}s): {v3[:60]}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(__file__).resolve().parent.parent / f"ab_test_describe_{ts}.md"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(f"# describe A/Bテスト結果 {ts}\n\n")
        fh.write(f"V2 = {OPENAI_GPT4O_DEPLOYMENT} / detail=high / 300tok  \n")
        fh.write(f"V3 = {mini} / detail=low / 150tok(few-shotなし素の比較)\n\n")
        for name, v2, t2, v3, t3 in rows:
            fh.write(f"## {name}\n\n")
            fh.write(f"- **V2** ({t2:.1f}s): {v2}\n")
            fh.write(f"- **V3** ({t3:.1f}s): {v3}\n\n")
        avg2 = sum(r[2] for r in rows) / len(rows)
        avg3 = sum(r[4] for r in rows) / len(rows)
        fh.write(f"---\n平均応答: V2 {avg2:.1f}s / V3 {avg3:.1f}s\n")
    print(f"[DONE] {out}")


if __name__ == "__main__":
    main()
