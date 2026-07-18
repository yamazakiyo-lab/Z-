"""氏名突合の診断ツール（未利用通知の照合が合わない原因を切り分ける）。

デスクトップ（GRAPH_* が .env にある環境）で実行:
    python diag_name_match.py

出力:
  - LW登録ユーザーごとに「一致/未照合」と、未照合ならEntra側の近い名前の候補Top3
  - name_match_report.csv （LW氏名 / 正規化 / 判定 / EntraUPN / 候補）

これで各未照合者が
  (A) Entraに個人アカウントが無い（候補が全然出ない・事業所）
  (B) 氏名フォーマット違い（似た候補が出る：ローマ字・旧字新字・空白/中黒など）
のどちらかを判別できる。
"""
from __future__ import annotations

import csv
import difflib
from pathlib import Path

import lw_annotation_bot as bot


def main() -> None:
    state = bot._load_annotation_state()
    users = state.get("users", [])
    names = bot._load_user_names()          # {lw_user_id: 苗字名前}
    entra = bot._load_entra_name_upn()      # {正規化表示名: UPN}
    if not entra:
        print("[ERROR] Entraユーザーを取得できませんでした（GRAPH_* を確認）")
        return

    entra_norm_keys = list(entra.keys())
    # 表示用に「正規化名 -> UPN」だけでなく候補提示に使う
    print(f"LW登録ユーザー: {len(users)} 名 / Entra取得: {len(entra)} 名\n")

    rows = []
    matched = unmatched = 0
    for uid in users:
        name = names.get(uid, "")
        nkey = bot._norm_name(name)
        upn = entra.get(nkey, "")
        if upn:
            matched += 1
            rows.append([name, nkey, "一致", upn, ""])
            continue
        unmatched += 1
        # 近いEntra名の候補Top3（フォーマット違いの発見用）
        cand = difflib.get_close_matches(nkey, entra_norm_keys, n=3, cutoff=0.4)
        cand_disp = " / ".join(f"{c}→{entra[c]}" for c in cand) if cand else "(候補なし＝Entraに存在しない可能性)"
        print(f"未照合: {name}  → 候補: {cand_disp}")
        rows.append([name, nkey, "未照合", "", cand_disp])

    out = Path(__file__).with_name("name_match_report.csv")
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["LW氏名", "正規化名", "判定", "EntraUPN", "候補(未照合時)"])
        w.writerows(rows)

    print(f"\n===== 集計: 一致 {matched} / 未照合 {unmatched} =====")
    print(f"[CSV] {out.name}")


if __name__ == "__main__":
    main()
