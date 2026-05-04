import os
import re
import sys

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


FONT_CANDIDATES = [
    r"C:\Windows\Fonts\NotoSansJP-VF.ttf",
    r"C:\Windows\Fonts\msgothic.ttc",
    r"C:\Windows\Fonts\meiryo.ttc",
]

SECTION_INDENT = 14
NESTED_LINE_INDENT = 12
BODY_OFFSET = 10
RIGHT_MARGIN = 28
PREFERRED_BREAK_AFTER = set(" 、。，．,.;:：)]）】」』〉》！？!?")


def resolve_font_path():
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    raise FileNotFoundError("Japanese font not found in Windows fonts directory")


def get_numbering_level(text):
    match = re.match(r"^(\d+(?:-\d+)*)\.\s*", text)
    if not match:
        return None
    return match.group(1).count("-")


def to_circled_number(value):
    if 1 <= value <= 20:
        return chr(9311 + value)
    return f"({value})"


def md_to_blocks(md):
    blocks = []
    in_code = False
    toc_mode = False
    toc_finished = False
    for raw in md.splitlines():
        line = raw.rstrip("\n")
        if line.startswith("```"):
            in_code = not in_code
            if not in_code:
                blocks.append({"type": "blank", "text": ""})
            continue
        if in_code:
            blocks.append({"type": "code", "text": line, "extra_indent": 0})
            continue
        if not line.strip():
            blocks.append({"type": "blank", "text": ""})
            continue
        heading_match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading_match:
            level = len(heading_match.group(1))
            text = heading_match.group(2).strip()
            if text == "目次":
                toc_mode = True
            elif toc_mode and not toc_finished and re.match(r"^\d+\.\s*", text):
                blocks.append({"type": "pagebreak", "text": ""})
                toc_finished = True
                toc_mode = False

            numbering_level = get_numbering_level(text)
            if level == 1:
                blocks.append({"type": "title", "text": text, "section_level": numbering_level})
            elif level == 2:
                blocks.append({"type": "heading", "text": text, "section_level": numbering_level})
            else:
                blocks.append({"type": "subheading", "text": text, "section_level": numbering_level})
            continue

        if toc_mode and not toc_finished:
            stripped = line.lstrip()
            leading_spaces = len(line) - len(stripped)
            toc_level = 0
            if leading_spaces >= 4:
                toc_level = 2
            elif leading_spaces >= 2:
                toc_level = 1
            blocks.append({"type": f"toc{toc_level}", "text": stripped, "section_level": toc_level})
            continue

        stripped = line.lstrip()
        leading_spaces = len(line) - len(stripped)
        if leading_spaces >= 4:
            blocks.append({"type": "indent2", "text": stripped, "extra_indent": NESTED_LINE_INDENT * 2})
            continue
        if leading_spaces >= 2:
            blocks.append({"type": "indent1", "text": stripped, "extra_indent": NESTED_LINE_INDENT})
            continue
        if stripped.startswith("- "):
            blocks.append({"type": "listitem", "text": stripped[2:], "extra_indent": NESTED_LINE_INDENT})
            continue
        ordered_match = re.match(r"^(\d+)\.\s+(.*)$", stripped)
        if ordered_match:
            marker_no = int(ordered_match.group(1))
            item_text = ordered_match.group(2)
            blocks.append({
                "type": "ordereditem",
                "text": item_text,
                "marker": to_circled_number(marker_no),
                "extra_indent": NESTED_LINE_INDENT,
            })
            continue
        blocks.append({"type": "body", "text": line, "extra_indent": 0})
    return blocks


def wrap_text(text, font_name, font_size, max_width):
    if not text:
        return [""]
    remaining = text.strip()
    wrapped = []

    while remaining:
        if pdfmetrics.stringWidth(remaining, font_name, font_size) <= max_width:
            wrapped.append(remaining)
            break

        current = ""
        last_fit = 0
        preferred_break = 0

        for index, ch in enumerate(remaining, 1):
            candidate = current + ch
            if pdfmetrics.stringWidth(candidate, font_name, font_size) > max_width:
                break
            current = candidate
            last_fit = index
            if ch in PREFERRED_BREAK_AFTER:
                preferred_break = index

        cut = preferred_break or last_fit
        if cut <= 0:
            cut = 1

        line = remaining[:cut].rstrip()
        if not line:
            line = remaining[:last_fit].rstrip() or remaining[:1]
            cut = len(line)

        wrapped.append(line)
        remaining = remaining[cut:].lstrip()

    return wrapped


def write_simple_pdf(blocks, outpath):
    font_path = resolve_font_path()
    font_name = "WorkspaceJapanese"
    pdfmetrics.registerFont(TTFont(font_name, font_path))

    page_width, page_height = A4
    margin_x = 72
    margin_y = 48
    max_width = page_width - margin_x - RIGHT_MARGIN

    style = {
        "title": {"font_size": 20, "leading": 27},
        "heading": {"font_size": 15, "leading": 22},
        "subheading": {"font_size": 13, "leading": 20},
        "toc0": {"font_size": 15, "leading": 22},
        "toc1": {"font_size": 13, "leading": 19},
        "toc2": {"font_size": 11.5, "leading": 17},
        "number": {"font_size": 11.5, "leading": 17},
        "ordereditem": {"font_size": 11.5, "leading": 17},
        "listitem": {"font_size": 11.5, "leading": 17},
        "indent1": {"font_size": 11.5, "leading": 17},
        "indent2": {"font_size": 11.5, "leading": 17},
        "body": {"font_size": 11.5, "leading": 17},
        "code": {"font_size": 10.5, "leading": 15},
        "blank": {"font_size": 11.5, "leading": 11},
    }

    pdf = canvas.Canvas(outpath, pagesize=A4)
    y = page_height - margin_y
    current_section_level = 0

    def draw_underlines(x, y_pos, line_width, count=1):
        for index in range(count):
            offset = 2 + (index * 2.5)
            pdf.line(x, y_pos - offset, x + line_width, y_pos - offset)

    def ensure_space(required):
        nonlocal y
        if y - required < margin_y:
            pdf.showPage()
            pdf.setFont(font_name, 11.5)
            y = page_height - margin_y

    pdf.setTitle("USER MANUAL")

    for block in blocks:
        block_type = block["type"]
        text = block["text"]

        if block_type == "pagebreak":
            pdf.showPage()
            pdf.setFont(font_name, 11.5)
            y = page_height - margin_y
            current_section_level = 0
            continue

        current = style[block_type]
        font_size = current["font_size"]
        leading = current["leading"]
        pdf.setFont(font_name, font_size)

        if block_type == "blank":
            y -= leading
            continue

        section_level = block.get("section_level")
        if section_level is not None:
            current_section_level = section_level

        numbering_level = get_numbering_level(text)

        if block_type in {"title", "heading", "subheading"}:
            base_indent = (section_level or 0) * SECTION_INDENT
        elif block_type in {"toc0", "toc1", "toc2"}:
            base_indent = (section_level or 0) * SECTION_INDENT
        elif numbering_level is not None:
            base_indent = numbering_level * SECTION_INDENT
        else:
            base_indent = (current_section_level * SECTION_INDENT) + BODY_OFFSET

        extra_indent = block.get("extra_indent", 0)

        if block_type in {"title", "heading", "subheading", "toc0", "toc1", "toc2"} or numbering_level is not None:
            extra_indent = 0

        x = margin_x + base_indent + extra_indent
        available_width = page_width - x - RIGHT_MARGIN

        if block_type in {"listitem", "ordereditem"}:
            bullet_x = x
            text_x = x + 16
            available_width = page_width - text_x - RIGHT_MARGIN
            wrapped = wrap_text(text, font_name, font_size, available_width)
            ensure_space(len(wrapped) * leading + 4)
            for index, line in enumerate(wrapped):
                if index == 0:
                    marker = "・" if block_type == "listitem" else block.get("marker", "・")
                    pdf.drawString(bullet_x, y, marker)
                pdf.drawString(text_x, y, line)
                y -= leading
            continue

        wrapped = wrap_text(text, font_name, font_size, available_width)
        ensure_space(len(wrapped) * leading + 4)

        underline_count = 0
        if block_type == "title":
            underline_count = 2
        elif block_type in {"heading", "subheading"} and section_level is not None:
            underline_count = 1
        elif block_type == "toc0":
            underline_count = 1

        for index, line in enumerate(wrapped):
            pdf.drawString(x, y, line)
            if underline_count and index == len(wrapped) - 1:
                line_width = pdfmetrics.stringWidth(line, font_name, font_size)
                draw_underlines(x, y, line_width, count=underline_count)
            y -= leading

        if block_type in {"title", "heading", "subheading"}:
            y -= 4

    pdf.save()

def main(md_path, out_pdf):
    if not os.path.exists(md_path):
        print('MD not found:', md_path)
        return 2
    with open(md_path, 'r', encoding='utf-8') as f:
        md = f.read()
    blocks = md_to_blocks(md)
    write_simple_pdf(blocks, out_pdf)
    print('Wrote PDF:', out_pdf)
    return 0

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('Usage: md_to_simple_pdf.py input.md output.pdf')
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
