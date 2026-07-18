"""マニュアル(.docx) → アプリ表示用Markdown(.md) 変換スクリプト。

TSEG WORKS のマニュアルページは、PDFビューアに頼らず「アプリ内でそのまま読める」形に
するため、.docx の本文を Markdown に変換して static/ に出力する。
（PDFはダウンロード用に別途 static/user_manual.pdf / ops_manual.pdf を置く）

使い方（マニュアル(.docx)を更新したら実行し、生成物をコミットする）:
    python build_manual_md.py

出力:
    static/user_manual.md   ← TSEG WORKS_利用者マニュアル.docx
    static/ops_manual.md    ← 共有フォルダ整理プログラム_運用マニュアル.docx

表紙と目次は、アプリ側で見出しが並ぶので省き、本文（最初の章見出し）から出力する。
"""
from __future__ import annotations

from pathlib import Path

import docx
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

BASE = Path(__file__).resolve().parent
JOBS = [
    ("TSEG WORKS_利用者マニュアル.docx", "static/user_manual.md"),
    ("共有フォルダ整理プログラム_運用マニュアル.docx", "static/ops_manual.md"),
]


def iter_blocks(doc):
    """段落と表を、文書に出てくる順番どおりに取り出す。"""
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            yield Table(child, doc)


def style_name(p: Paragraph) -> str:
    try:
        return p.style.name or ""
    except Exception:
        return ""


def table_to_md(tb: Table) -> str:
    rows = [[c.text.strip().replace("\n", " ") for c in r.cells] for r in tb.rows]
    rows = [r for r in rows if any(x for x in r)]
    if not rows:
        return ""
    head, body = rows[0], rows[1:]
    md = ["| " + " | ".join(head) + " |", "| " + " | ".join(["---"] * len(head)) + " |"]
    for r in body:
        r = (r + [""] * len(head))[: len(head)]
        md.append("| " + " | ".join(r) + " |")
    return "\n".join(md)


def docx_to_md(path: Path) -> str:
    doc = docx.Document(str(path))
    blocks = list(iter_blocks(doc))

    # 「目次」見出しの後、最初の章見出し（Heading）＝本文開始位置を探す
    start = 0
    toc_i = next(
        (i for i, b in enumerate(blocks)
         if isinstance(b, Paragraph) and b.text.strip() == "目次"),
        None,
    )
    if toc_i is not None:
        for j in range(toc_i + 1, len(blocks)):
            b = blocks[j]
            if isinstance(b, Paragraph) and style_name(b).startswith("Heading"):
                start = j
                break

    out: list[str] = []
    for b in blocks[start:]:
        if isinstance(b, Table):
            t = table_to_md(b)
            if t:
                out.append(t)
            continue
        text = b.text.strip()
        if not text:
            continue
        s = style_name(b)
        if s.startswith("Heading 1"):
            out.append(f"## {text}")
        elif s.startswith("Heading 2"):
            out.append(f"### {text}")
        elif s.startswith("Heading"):
            out.append(f"#### {text}")
        elif s.startswith("List"):
            out.append(f"- {text}")
        elif text.startswith("・"):
            out.append(f"- {text[1:].strip()}")
        else:
            out.append(text)
    return "\n\n".join(out) + "\n"


def main() -> None:
    for src, dst in JOBS:
        sp, dp = BASE / src, BASE / dst
        if not sp.exists():
            print(f"[SKIP] 見つかりません: {src}")
            continue
        md = docx_to_md(sp)
        dp.parent.mkdir(parents=True, exist_ok=True)
        dp.write_text(md, encoding="utf-8")
        print(f"[OK] {src} → {dst}  ({len(md):,} 文字)")


if __name__ == "__main__":
    main()
