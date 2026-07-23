from __future__ import annotations

import argparse
import html
import re
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import portrait
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Frame,
    HRFlowable,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents


PAGE_SIZE = portrait((108 * mm, 192 * mm))
PAGE_WIDTH, PAGE_HEIGHT = PAGE_SIZE
LEFT_MARGIN = 10 * mm
RIGHT_MARGIN = 10 * mm
TOP_MARGIN = 13 * mm
BOTTOM_MARGIN = 13 * mm
CONTENT_WIDTH = PAGE_WIDTH - LEFT_MARGIN - RIGHT_MARGIN

INK = colors.HexColor("#18212F")
MUTED = colors.HexColor("#607086")
BLUE = colors.HexColor("#2563EB")
BLUE_DARK = colors.HexColor("#1847A8")
BLUE_SOFT = colors.HexColor("#EAF2FF")
CYAN_SOFT = colors.HexColor("#E8F8FA")
LINE = colors.HexColor("#D8E1EC")
PAPER = colors.HexColor("#FFFFFF")
CODE_BG = colors.HexColor("#F3F6FA")
QUOTE_BG = colors.HexColor("#F2F7FF")


def register_fonts() -> None:
    font_dir = Path(r"C:\Windows\Fonts")
    regular = font_dir / "Deng.ttf"
    bold = font_dir / "Dengb.ttf"
    mono = font_dir / "simhei.ttf"
    for path in (regular, bold, mono):
        if not path.exists():
            raise FileNotFoundError(f"缺少中文字体：{path}")
    pdfmetrics.registerFont(TTFont("MobileCN", str(regular)))
    pdfmetrics.registerFont(TTFont("MobileCN-Bold", str(bold)))
    pdfmetrics.registerFont(TTFont("MobileCN-Mono", str(mono)))
    pdfmetrics.registerFontFamily(
        "MobileCN",
        normal="MobileCN",
        bold="MobileCN-Bold",
        italic="MobileCN",
        boldItalic="MobileCN-Bold",
    )


def create_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "cover_title": ParagraphStyle(
            "CoverTitle",
            parent=base["Title"],
            fontName="MobileCN-Bold",
            fontSize=23,
            leading=31,
            textColor=INK,
            alignment=TA_LEFT,
            spaceAfter=8 * mm,
        ),
        "cover_subtitle": ParagraphStyle(
            "CoverSubtitle",
            fontName="MobileCN",
            fontSize=11.5,
            leading=18,
            textColor=MUTED,
            alignment=TA_LEFT,
        ),
        "toc_title": ParagraphStyle(
            "TocTitle",
            fontName="MobileCN-Bold",
            fontSize=20,
            leading=26,
            textColor=INK,
            spaceAfter=6 * mm,
        ),
        "h2": ParagraphStyle(
            "H2",
            fontName="MobileCN-Bold",
            fontSize=17,
            leading=23,
            textColor=BLUE_DARK,
            spaceBefore=1 * mm,
            spaceAfter=4 * mm,
            keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "H3",
            fontName="MobileCN-Bold",
            fontSize=13.5,
            leading=19,
            textColor=INK,
            spaceBefore=4.5 * mm,
            spaceAfter=2.2 * mm,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "BodyCN",
            fontName="MobileCN",
            fontSize=11.2,
            leading=17.2,
            textColor=INK,
            alignment=TA_LEFT,
            wordWrap="CJK",
            allowWidows=0,
            spaceAfter=2.4 * mm,
        ),
        "body_label": ParagraphStyle(
            "BodyLabelCN",
            fontName="MobileCN-Bold",
            fontSize=11.2,
            leading=17.2,
            textColor=INK,
            alignment=TA_LEFT,
            wordWrap="CJK",
            allowWidows=0,
            keepWithNext=True,
            spaceBefore=1 * mm,
            spaceAfter=1.2 * mm,
        ),
        "small": ParagraphStyle(
            "SmallCN",
            fontName="MobileCN",
            fontSize=9.4,
            leading=14,
            textColor=MUTED,
            wordWrap="CJK",
        ),
        "bullet": ParagraphStyle(
            "BulletCN",
            fontName="MobileCN",
            fontSize=11,
            leading=16.5,
            textColor=INK,
            leftIndent=5 * mm,
            firstLineIndent=-3.5 * mm,
            bulletIndent=1 * mm,
            wordWrap="CJK",
            spaceAfter=1.2 * mm,
        ),
        "quote": ParagraphStyle(
            "QuoteCN",
            fontName="MobileCN",
            fontSize=10.8,
            leading=16.5,
            textColor=BLUE_DARK,
            leftIndent=4.5 * mm,
            rightIndent=2.5 * mm,
            borderColor=BLUE,
            borderWidth=0,
            borderPadding=(3 * mm, 3 * mm, 3 * mm, 4 * mm),
            backColor=QUOTE_BG,
            wordWrap="CJK",
            spaceBefore=1 * mm,
            spaceAfter=3 * mm,
        ),
        "code": ParagraphStyle(
            "CodeCN",
            fontName="MobileCN-Mono",
            fontSize=8.6,
            leading=12.2,
            textColor=colors.HexColor("#203044"),
            leftIndent=2.5 * mm,
            rightIndent=2.5 * mm,
            borderPadding=3 * mm,
            backColor=CODE_BG,
            borderColor=LINE,
            borderWidth=0.4,
            borderRadius=2,
            wordWrap="CJK",
            spaceBefore=1 * mm,
            spaceAfter=3 * mm,
        ),
        "code_label": ParagraphStyle(
            "CodeLabel",
            fontName="MobileCN-Bold",
            fontSize=7.5,
            leading=9,
            textColor=MUTED,
            spaceAfter=1 * mm,
        ),
        "table_header": ParagraphStyle(
            "TableHeader",
            fontName="MobileCN-Bold",
            fontSize=8.7,
            leading=12,
            textColor=colors.white,
            wordWrap="CJK",
        ),
        "table_cell": ParagraphStyle(
            "TableCell",
            fontName="MobileCN",
            fontSize=8.6,
            leading=12.2,
            textColor=INK,
            wordWrap="CJK",
        ),
        "table_card": ParagraphStyle(
            "TableCard",
            fontName="MobileCN",
            fontSize=9.4,
            leading=14.2,
            textColor=INK,
            backColor=CODE_BG,
            borderColor=LINE,
            borderWidth=0.5,
            borderPadding=3 * mm,
            wordWrap="CJK",
            spaceAfter=2 * mm,
        ),
    }


def inline_markup(text: str) -> str:
    escaped = html.escape(text.strip())
    escaped = re.sub(
        r"\[([^\]]+)\]\((https?://[^)]+)\)",
        r'<link href="\2" color="#2563EB"><u>\1</u></link>',
        escaped,
    )
    escaped = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", escaped)
    escaped = re.sub(
        r"`([^`]+)`",
        r'<font name="MobileCN-Mono" color="#1847A8">\1</font>',
        escaped,
    )
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", escaped)
    return escaped


def split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def is_table_separator(line: str) -> bool:
    cells = split_table_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells)


def render_table(rows: list[list[str]], styles: dict[str, ParagraphStyle]):
    if not rows or len(rows) < 2:
        return []
    header = rows[0]
    body = rows[1:]
    column_count = len(header)
    if column_count >= 4:
        cards = []
        for row in body:
            padded = row + [""] * (column_count - len(row))
            parts = []
            for label, value in zip(header, padded):
                parts.append(f"<b>{inline_markup(label)}</b>：{inline_markup(value)}")
            cards.append(Paragraph("<br/>".join(parts), styles["table_card"]))
        if len(cards) >= 2:
            return [*cards[:-2], KeepTogether(cards[-2:])]
        return cards

    data = [
        [Paragraph(inline_markup(cell), styles["table_header"]) for cell in header]
    ]
    for row in body:
        padded = row + [""] * (column_count - len(row))
        data.append(
            [Paragraph(inline_markup(cell), styles["table_cell"]) for cell in padded]
        )

    if column_count == 2:
        widths = [CONTENT_WIDTH * 0.34, CONTENT_WIDTH * 0.66]
    else:
        widths = [CONTENT_WIDTH / column_count] * column_count
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT", splitByRow=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), BLUE_DARK),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.45, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [PAPER, colors.HexColor("#F8FAFD")]),
            ]
        )
    )
    return [table, Spacer(1, 3 * mm)]


class MobileDocTemplate(BaseDocTemplate):
    def __init__(
        self,
        filename: str,
        styles: dict[str, ParagraphStyle],
        document_title: str,
        header_title: str,
        subject: str,
    ):
        super().__init__(
            filename,
            pagesize=PAGE_SIZE,
            leftMargin=LEFT_MARGIN,
            rightMargin=RIGHT_MARGIN,
            topMargin=TOP_MARGIN,
            bottomMargin=BOTTOM_MARGIN,
            title=document_title,
            author="DeepSearcher Study",
            subject=subject,
        )
        self.styles = styles
        self.header_title = header_title
        frame = Frame(
            LEFT_MARGIN,
            BOTTOM_MARGIN,
            CONTENT_WIDTH,
            PAGE_HEIGHT - TOP_MARGIN - BOTTOM_MARGIN,
            id="mobile",
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
        )
        self.addPageTemplates(PageTemplate(id="mobile", frames=[frame], onPage=self.draw_page))
        self._bookmark_index = 0

    def beforeDocument(self) -> None:
        """每次目录排版前重置书签编号，避免多轮构建时目录键持续变化。"""
        self._bookmark_index = 0

    def draw_page(self, canvas, doc) -> None:
        canvas.saveState()
        if doc.page > 2:
            canvas.setFont("MobileCN", 7.6)
            canvas.setFillColor(MUTED)
            canvas.drawString(LEFT_MARGIN, PAGE_HEIGHT - 7.5 * mm, self.header_title)
            canvas.setStrokeColor(LINE)
            canvas.setLineWidth(0.35)
            canvas.line(
                LEFT_MARGIN,
                PAGE_HEIGHT - 9.2 * mm,
                PAGE_WIDTH - RIGHT_MARGIN,
                PAGE_HEIGHT - 9.2 * mm,
            )
        canvas.setFont("MobileCN", 7.8)
        canvas.setFillColor(MUTED)
        canvas.drawCentredString(PAGE_WIDTH / 2, 6.2 * mm, str(doc.page))
        canvas.restoreState()

    def afterFlowable(self, flowable) -> None:
        if not isinstance(flowable, Paragraph):
            return
        if flowable.style.name not in {"H2", "H3"}:
            return
        level = 0 if flowable.style.name == "H2" else 1
        text = flowable.getPlainText()
        key = f"heading-{self._bookmark_index}"
        self._bookmark_index += 1
        self.canv.bookmarkPage(key)
        self.canv.addOutlineEntry(text, key, level=level, closed=level == 0)
        if level == 0:
            self.notify("TOCEntry", (level, text, self.page, key))


def parse_markdown(markdown_text: str, styles: dict[str, ParagraphStyle]):
    lines = markdown_text.splitlines()
    story = []
    index = 0
    first_h2 = True
    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()

        if not stripped:
            index += 1
            continue

        if stripped.startswith("```"):
            language = stripped[3:].strip()
            index += 1
            code_lines = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index].rstrip())
                index += 1
            index += 1
            code_text = "<br/>".join(
                html.escape(line).replace(" ", "&#160;") if line else "&#160;"
                for line in code_lines
            )
            block = []
            if language:
                block.append(Paragraph(language.upper(), styles["code_label"]))
            block.append(Paragraph(code_text or "&#160;", styles["code"]))
            story.append(KeepTogether(block))
            continue

        if stripped.startswith("# "):
            index += 1
            continue

        if stripped.startswith("## "):
            if not first_h2:
                # 手机阅读版不强制每章另起一页；剩余空间不足时再换页，
                # 避免上一章仅有一两行落在空白页。
                story.append(CondPageBreak(50 * mm))
            first_h2 = False
            story.append(Paragraph(inline_markup(stripped[3:]), styles["h2"]))
            index += 1
            continue

        if stripped.startswith("### "):
            story.append(Paragraph(inline_markup(stripped[4:]), styles["h3"]))
            index += 1
            continue

        if stripped.startswith(">"):
            quote_lines = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote_lines.append(lines[index].strip().lstrip(">").strip())
                index += 1
            story.append(Paragraph(inline_markup(" ".join(quote_lines)), styles["quote"]))
            continue

        if stripped.startswith("|") and index + 1 < len(lines) and is_table_separator(lines[index + 1]):
            table_rows = [split_table_row(stripped)]
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_rows.append(split_table_row(lines[index]))
                index += 1
            story.extend(render_table(table_rows, styles))
            continue

        bullet_match = re.match(r"^[-*]\s+(.+)$", stripped)
        numbered_match = re.match(r"^(\d+)\.\s+(.+)$", stripped)
        if bullet_match:
            story.append(
                Paragraph(inline_markup(bullet_match.group(1)), styles["bullet"], bulletText="•")
            )
            index += 1
            continue
        if numbered_match:
            story.append(
                Paragraph(
                    inline_markup(numbered_match.group(2)),
                    styles["bullet"],
                    bulletText=f"{numbered_match.group(1)}.",
                )
            )
            index += 1
            continue

        if stripped == "---":
            story.append(Spacer(1, 1 * mm))
            story.append(HRFlowable(width="100%", thickness=0.5, color=LINE))
            story.append(Spacer(1, 2 * mm))
            index += 1
            continue

        if re.fullmatch(r"\*\*[^*]+[：:]\*\*", stripped):
            story.append(Paragraph(inline_markup(stripped), styles["body_label"]))
            index += 1
            continue

        paragraph_lines = [stripped]
        index += 1
        while index < len(lines):
            nxt = lines[index].strip()
            if not nxt:
                index += 1
                break
            if (
                nxt.startswith("#")
                or nxt.startswith("```")
                or nxt.startswith(">")
                or nxt.startswith("|")
                or re.match(r"^[-*]\s+", nxt)
                or re.match(r"^\d+\.\s+", nxt)
                or nxt == "---"
            ):
                break
            paragraph_lines.append(nxt)
            index += 1
        story.append(Paragraph(inline_markup(" ".join(paragraph_lines)), styles["body"]))

    return story


def build_pdf(
    source: Path,
    output: Path,
    *,
    cover_title: str,
    cover_subtitle: str,
    scope_label: str,
    scope: str,
    method_label: str,
    method: str,
    header_title: str,
    document_title: str,
    subject: str,
) -> None:
    register_fonts()
    styles = create_styles()
    output.parent.mkdir(parents=True, exist_ok=True)
    markdown_text = source.read_text(encoding="utf-8")

    story = [
        Spacer(1, 16 * mm),
        Paragraph("DeepSearcher", styles["cover_subtitle"]),
        Spacer(1, 3 * mm),
        Paragraph(inline_markup(cover_title), styles["cover_title"]),
        Paragraph(inline_markup(cover_subtitle), styles["cover_subtitle"]),
        Spacer(1, 12 * mm),
        Table(
            [
                [Paragraph(inline_markup(scope_label), styles["small"]), Paragraph(inline_markup(scope), styles["small"])],
                [Paragraph(inline_markup(method_label), styles["small"]), Paragraph(inline_markup(method), styles["small"])],
                [Paragraph("生成日期", styles["small"]), Paragraph(date.today().isoformat(), styles["small"])],
            ],
            colWidths=[CONTENT_WIDTH * 0.25, CONTENT_WIDTH * 0.75],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), BLUE_SOFT),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C9D9F2")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            ),
        ),
        Spacer(1, 13 * mm),
        Paragraph("适合手机竖屏阅读；目录与 PDF 书签可直接跳转章节。", styles["small"]),
        PageBreak(),
        Paragraph("目录", styles["toc_title"]),
    ]

    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle(
            "TOCLevel1",
            fontName="MobileCN",
            fontSize=10.8,
            leading=17,
            leftIndent=0,
            firstLineIndent=0,
            textColor=INK,
            spaceBefore=2,
        )
    ]
    story.extend([toc, PageBreak()])
    story.extend(parse_markdown(markdown_text, styles))

    doc = MobileDocTemplate(
        str(output),
        styles,
        document_title=document_title,
        header_title=header_title,
        subject=subject,
    )
    doc.multiBuild(story)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 DeepSearcher 手机阅读版学习讲义 PDF")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("docs/学习计划/第一阶段学习讲义.md"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/pdf/DeepSearcher第一阶段学习讲义-手机阅读版.pdf"),
    )
    parser.add_argument("--cover-title", default="第一阶段学习讲义")
    parser.add_argument("--cover-subtitle", default="手机阅读版 · 完整对话讲解整理")
    parser.add_argument("--scope-label", default="学习范围")
    parser.add_argument("--scope", default="RAG 入库、向量检索、在线路由、DeepSearch、ChainOfRAG")
    parser.add_argument("--method-label", default="学习方式")
    parser.add_argument("--method", default="第一遍直接理解，第二遍模拟面试")
    parser.add_argument("--header-title", default="DeepSearcher 第一阶段学习讲义")
    parser.add_argument("--document-title", default="DeepSearcher 第一阶段学习讲义 - 手机阅读版")
    parser.add_argument("--subject", default="DeepSearcher AI 全栈第一阶段学习讲义")
    args = parser.parse_args()
    build_pdf(
        args.source.resolve(),
        args.output.resolve(),
        cover_title=args.cover_title,
        cover_subtitle=args.cover_subtitle,
        scope_label=args.scope_label,
        scope=args.scope,
        method_label=args.method_label,
        method=args.method,
        header_title=args.header_title,
        document_title=args.document_title,
        subject=args.subject,
    )
    print(args.output.resolve())


if __name__ == "__main__":
    main()
