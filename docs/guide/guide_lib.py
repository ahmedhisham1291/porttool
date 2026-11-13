"""Layout helpers for the PortTool engineer guide (reportlab Platypus).

Only standard PDF fonts are used (Helvetica, Courier), so keep the text inside
Latin-1: no arrows, no typographic symbols.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Flowable,
    Frame,
    Image,
    KeepTogether,
    LongTable,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    XPreformatted,
)
from reportlab.platypus.tableofcontents import TableOfContents

PAGE_W, PAGE_H = A4
MARGIN_X = 18 * mm
MARGIN_TOP = 20 * mm
MARGIN_BOTTOM = 18 * mm
FRAME_W = PAGE_W - 2 * MARGIN_X

NAVY = HexColor("#12304A")
TEAL = HexColor("#0E7490")
INK = HexColor("#1F2937")
MUTED = HexColor("#6B7280")
RULE = HexColor("#D5DAE1")
CODE_BG = HexColor("#F3F5F8")
ZEBRA = HexColor("#F7F9FC")
BLUE_BG, BLUE = HexColor("#E6F2FB"), HexColor("#1D6FA5")
AMBER_BG, AMBER = HexColor("#FDF3D7"), HexColor("#B7791F")
RED_BG, RED = HexColor("#FBE4E4"), HexColor("#B42318")
GREEN_BG, GREEN = HexColor("#E3F4EA"), HexColor("#1F7A4D")
GREY_BG = HexColor("#EEF0F3")

TITLE = "PortTool 0.1.0 Engineer Guide"

BODY = ParagraphStyle(
    "Body", fontName="Helvetica", fontSize=9.6, leading=13.6, textColor=INK, spaceAfter=6
)
H1 = ParagraphStyle(
    "H1", fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=NAVY, spaceAfter=10
)
H2 = ParagraphStyle(
    "H2",
    fontName="Helvetica-Bold",
    fontSize=13,
    leading=16,
    textColor=NAVY,
    spaceBefore=12,
    spaceAfter=5,
    keepWithNext=1,
)
H3 = ParagraphStyle(
    "H3",
    fontName="Helvetica-Bold",
    fontSize=10.4,
    leading=13.6,
    textColor=TEAL,
    spaceBefore=8,
    spaceAfter=3,
    keepWithNext=1,
)
BULLET = ParagraphStyle("Bullet", parent=BODY, leftIndent=13, bulletIndent=3, spaceAfter=3)
STEP = ParagraphStyle("Step", parent=BODY, leftIndent=17, bulletIndent=1, spaceAfter=4)
CELL = ParagraphStyle(
    "Cell", fontName="Helvetica", fontSize=8.2, leading=10.4, textColor=INK, spaceAfter=0
)
CELL_HEAD = ParagraphStyle("CellHead", parent=CELL, fontName="Helvetica-Bold", textColor=white)
CODE = ParagraphStyle(
    "Code",
    fontName="Courier",
    fontSize=7.5,
    leading=9.6,
    textColor=HexColor("#111827"),
    backColor=CODE_BG,
    borderColor=RULE,
    borderWidth=0.5,
    borderPadding=(5, 6, 5, 6),
    leftIndent=6,
    rightIndent=6,
    spaceBefore=8,
    spaceAfter=12,
)
CAPTION = ParagraphStyle(
    "Caption", parent=BODY, fontSize=8.2, leading=10.6, textColor=MUTED, spaceBefore=3
)
NOTE = ParagraphStyle("Note", parent=BODY, fontSize=9, leading=12.4, spaceAfter=0)
TOC0 = ParagraphStyle(
    "TOC0", fontName="Helvetica-Bold", fontSize=9.6, leading=12.6, textColor=NAVY, spaceBefore=5
)
TOC1 = ParagraphStyle(
    "TOC1", fontName="Helvetica", fontSize=8.6, leading=11, textColor=INK, leftIndent=14
)

CODE_MAX_CHARS = 104


def md(text: str) -> str:
    """Escape text for a Paragraph; `code` and **bold** are the only markup."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"`([^`]+)`", r'<font face="Courier">\1</font>', text)
    return re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)


def p(text: str) -> Paragraph:
    return Paragraph(md(text), BODY)


def h1(text: str) -> list[Flowable]:
    return [PageBreak(), Paragraph(md(text), H1), _Rule()]


def h2(text: str) -> Flowable:
    return Paragraph(md(text), H2)


def h3(text: str) -> Flowable:
    return Paragraph(md(text), H3)


def bullets(items: Sequence[str]) -> list[Flowable]:
    return [Paragraph(md(item), BULLET, bulletText="\u2022") for item in items]


def steps(items: Sequence[str], start: int = 1) -> list[Flowable]:
    return [
        Paragraph(md(item), STEP, bulletText=f"{number}.")
        for number, item in enumerate(items, start)
    ]


def code(text: str) -> Flowable:
    """A shaded block of fixed-width text. Long lines are wrapped with an indent."""
    lines: list[str] = []
    for line in text.strip("\n").split("\n"):
        if len(line) > CODE_MAX_CHARS:
            print(f"guide: wrapped code line ({len(line)} chars): {line[:60]}", file=sys.stderr)
        while len(line) > CODE_MAX_CHARS:
            lines.append(line[:CODE_MAX_CHARS])
            line = "      " + line[CODE_MAX_CHARS:]
        lines.append(line)
    text = "\n".join(lines).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return XPreformatted(text, CODE)


def note(kind: str, title: str, text: str) -> Flowable:
    """A coloured box. kind: info, warn, danger, ok."""
    background, bar = {
        "info": (BLUE_BG, BLUE),
        "warn": (AMBER_BG, AMBER),
        "danger": (RED_BG, RED),
        "ok": (GREEN_BG, GREEN),
    }[kind]
    body = Paragraph(f"<b>{md(title)}</b>&nbsp; {md(text)}", NOTE)
    table = Table([[body]], colWidths=[FRAME_W])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), background),
                ("LINEBEFORE", (0, 0), (0, -1), 3, bar),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return KeepTogether([Spacer(1, 3), table, Spacer(1, 8)])


def table(
    head: Sequence[str],
    rows: Sequence[Sequence[str]],
    widths: Sequence[float],
    *,
    after: float = 8,
) -> list[Flowable]:
    """A grid table. `widths` are shares of the text width; cells take md() markup."""
    total = float(sum(widths))
    col_widths = [FRAME_W * width / total for width in widths]
    data: list[list[Paragraph]] = [[Paragraph(md(cell), CELL_HEAD) for cell in head]]
    for row in rows:
        data.append([Paragraph(md(cell), CELL) for cell in row])
    grid = LongTable(data, colWidths=col_widths, repeatRows=1, splitByRow=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
        ("BOX", (0, 0), (-1, -1), 0.5, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.6),
    ]
    for index in range(2, len(data), 2):
        style.append(("BACKGROUND", (0, index), (-1, index), ZEBRA))
    grid.setStyle(TableStyle(style))
    return [Spacer(1, 2), grid, Spacer(1, after)]


def picture(path: str, caption: str) -> list[Flowable]:
    image = Image(path)
    scale = FRAME_W / image.imageWidth
    image.drawWidth = FRAME_W
    image.drawHeight = image.imageHeight * scale
    framed = Table([[image]], colWidths=[FRAME_W])
    framed.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, RULE),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return [KeepTogether([Spacer(1, 4), framed, Paragraph(md(caption), CAPTION)])]


def caption(text: str) -> Flowable:
    return Paragraph(md(text), CAPTION)


def gap(height: float = 6) -> Flowable:
    return Spacer(1, height)


def need(height_mm: float) -> Flowable:
    """Start a new page unless this much room is left."""
    return CondPageBreak(height_mm * mm)


class _Rule(Flowable):
    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        self.width = avail_width
        return avail_width, 8

    def draw(self) -> None:
        self.canv.setStrokeColor(TEAL)
        self.canv.setLineWidth(1.4)
        self.canv.line(0, 6, 46, 6)


# ---------------------------------------------------------------- diagrams


def _box(
    drawing: Drawing,
    x: float,
    y: float,
    w: float,
    h: float,
    title: str,
    lines: Sequence[str] = (),
    *,
    fill: HexColor = BLUE_BG,
    stroke: HexColor = BLUE,
    dashed: bool = False,
    title_size: float = 8.6,
) -> None:
    rect = Rect(x, y, w, h, rx=4, ry=4, fillColor=fill, strokeColor=stroke, strokeWidth=0.9)
    if dashed:
        rect.strokeDashArray = [3, 2]
    drawing.add(rect)
    line_height = 8.6
    block = title_size + 2 + line_height * len(lines)
    top = y + h / 2 + block / 2 - title_size + 1
    drawing.add(
        String(
            x + w / 2,
            top,
            title,
            fontName="Helvetica-Bold",
            fontSize=title_size,
            fillColor=NAVY,
            textAnchor="middle",
        )
    )
    for index, line in enumerate(lines):
        drawing.add(
            String(
                x + w / 2,
                top - 3 - line_height * (index + 1) + 1.5,
                line,
                fontName="Helvetica",
                fontSize=6.9,
                fillColor=INK,
                textAnchor="middle",
            )
        )


def _arrow(drawing: Drawing, points: Sequence[tuple[float, float]]) -> None:
    for (x1, y1), (x2, y2) in zip(points, points[1:], strict=False):
        drawing.add(Line(x1, y1, x2, y2, strokeColor=MUTED, strokeWidth=1.1))
    (x1, y1), (x2, y2) = points[-2], points[-1]
    size = 4.2
    if abs(x2 - x1) >= abs(y2 - y1):
        sign = 1 if x2 > x1 else -1
        head = [x2, y2, x2 - sign * size, y2 + size * 0.6, x2 - sign * size, y2 - size * 0.6]
    else:
        sign = 1 if y2 > y1 else -1
        head = [x2, y2, x2 - size * 0.6, y2 - sign * size, x2 + size * 0.6, y2 - sign * size]
    drawing.add(Polygon(head, fillColor=MUTED, strokeColor=MUTED, strokeWidth=0.5))


def cycle_diagram() -> Drawing:
    """The eight steps of one change cycle, two rows of four."""
    width, height = FRAME_W, 176
    drawing = Drawing(width, height)
    gap_x = 16.0
    box_w = (width - 3 * gap_x) / 4
    box_h = 62.0
    row1, row2 = height - box_h - 4, 12.0
    top = [
        ("1  Load", ["read all workspace ARXML", "build the model, fill the cache"], BLUE_BG, BLUE),
        ("2  Export", ["write the Excel workbook", "(current state + request rows)"], BLUE_BG, BLUE),
        ("3  Teams edit", ["add request rows in Excel", "or use the request form"], AMBER_BG, AMBER),
        ("4  Import", ["read one or more workbooks", "check sheets, rows and cells"], BLUE_BG, BLUE),
    ]
    bottom = [
        ("5  Validate (dry run)", ["AUTOSAR + project rules", "status per request, write plan"], BLUE_BG, BLUE),
        ("6  Apply", ["git branch, edit ARXML,", "verify by reload, commit"], GREEN_BG, GREEN),
        ("7  DaVinci steps", ["check, script, validate,", "generate (optional, off)"], GREY_BG, MUTED),
        ("8  Report", ["HTML report, Results sheet,", "packages for model owners"], BLUE_BG, BLUE),
    ]
    for index, (title, lines, fill, stroke) in enumerate(top):
        x = index * (box_w + gap_x)
        _box(drawing, x, row1, box_w, box_h, title, lines, fill=fill, stroke=stroke)
        if index:
            _arrow(drawing, [(x - gap_x + 1, row1 + box_h / 2), (x - 1, row1 + box_h / 2)])
    for index, (title, lines, fill, stroke) in enumerate(bottom):
        x = index * (box_w + gap_x)
        _box(
            drawing, x, row2, box_w, box_h, title, lines, fill=fill, stroke=stroke, dashed=index == 2
        )
        if index:
            _arrow(drawing, [(x - gap_x + 1, row2 + box_h / 2), (x - 1, row2 + box_h / 2)])
    last_x = 3 * (box_w + gap_x) + box_w / 2
    middle = (row1 + row2 + box_h) / 2
    _arrow(
        drawing,
        [(last_x, row1 - 1), (last_x, middle), (box_w / 2, middle), (box_w / 2, row2 + box_h + 1)],
    )
    return drawing


def architecture_diagram() -> Drawing:
    """Packages and what they talk to."""
    width, height = FRAME_W, 250
    drawing = Drawing(width, height)
    left, inner = 0.0, width - 118
    row_h = 38.0
    y = height - row_h - 2
    half = (inner - 10) / 2
    _box(drawing, left, y, half, row_h, "gui", ["page in your browser, 127.0.0.1 only"])
    _box(drawing, left + half + 10, y, half, row_h, "cli", ["porttool <command>"])
    y -= row_h + 14
    _box(
        drawing,
        left,
        y,
        inner,
        row_h,
        "pipeline",
        ["the service behind GUI and CLI: jobs, apply transaction, journal, git, rollback, report"],
        fill=GREEN_BG,
        stroke=GREEN,
    )
    _arrow(drawing, [(left + half / 2, y + row_h + 14), (left + half / 2, y + row_h + 1)])
    _arrow(
        drawing,
        [(left + half + 10 + half / 2, y + row_h + 14), (left + half + 10 + half / 2, y + row_h + 1)],
    )
    y -= row_h + 14
    names = [
        ("reader", ["ARXML to model", "parallel parse, cache"]),
        ("workbook", ["Excel export", "and import"]),
        ("validator", ["rules, routing,", "pending queue"]),
        ("writer", ["in-place ARXML", "edits, delta"]),
        ("davinci", ["runs the Vector", "command-line tools"]),
    ]
    small_gap = 8.0
    small_w = (inner - small_gap * 4) / 5
    for index, (name, lines) in enumerate(names):
        x = left + index * (small_w + small_gap)
        _box(drawing, x, y, small_w, row_h + 4, name, lines)
        _arrow(drawing, [(x + small_w / 2, y + row_h + 4 + 10), (x + small_w / 2, y + row_h + 5)])
    y -= row_h + 14
    _box(
        drawing,
        left,
        y,
        half,
        row_h,
        "engine",
        ["one asyncio loop in a background thread", "+ thread pool; progress, cancel"],
        fill=GREY_BG,
        stroke=MUTED,
    )
    _box(
        drawing,
        left + half + 10,
        y,
        half,
        row_h,
        "core",
        ["shared contracts: model, operations,", "findings, errors, configuration"],
        fill=GREY_BG,
        stroke=MUTED,
    )
    y -= row_h + 8
    _box(
        drawing,
        left,
        y,
        inner,
        row_h - 8,
        "discovery",
        ["porttool discover: read-only study of a real repository and of the Vector tools' help text"],
        fill=AMBER_BG,
        stroke=AMBER,
    )
    # the outside world
    x = inner + 18
    outside_w = width - x
    items = [
        ("ARXML files", "workspace, ECUC"),
        ("Excel workbooks", "requests in, results out"),
        ("git", "branch, commit, restore"),
        ("Vector tools", "Developer + Configurator CLIs"),
        ("Local files", "logs, journal, cache"),
    ]
    item_h = 36.0
    spacing = (height - 4 - item_h * len(items)) / (len(items) - 1)
    for index, (title, line) in enumerate(items):
        item_y = height - 2 - item_h - index * (item_h + spacing)
        _box(drawing, x, item_y, outside_w, item_h, title, [line], fill=white, stroke=MUTED, dashed=True)
    drawing.add(Line(inner + 9, 4, inner + 9, height - 2, strokeColor=RULE, strokeWidth=0.8))
    return drawing


def apply_diagram() -> Drawing:
    """The thirteen steps of the apply transaction."""
    width, height = FRAME_W, 92
    drawing = Drawing(width, height)
    names = [
        "preflight",
        "branch",
        "pending",
        "write",
        "check",
        "script",
        "validate",
        "generate",
        "verify",
        "packages",
        "report",
        "commit",
        "close",
    ]
    gap_x = 3.0
    box_w = (width - gap_x * (len(names) - 1)) / len(names)
    y, box_h = 44.0, 26.0
    for index, name in enumerate(names):
        x = index * (box_w + gap_x)
        optional = name in ("check", "script", "validate", "generate")
        fill, stroke = (GREY_BG, MUTED) if optional else (GREEN_BG, GREEN)
        if name == "preflight":
            fill, stroke = BLUE_BG, BLUE
        rect = Rect(x, y, box_w, box_h, rx=3, ry=3, fillColor=fill, strokeColor=stroke, strokeWidth=0.8)
        if optional:
            rect.strokeDashArray = [3, 2]
        drawing.add(rect)
        drawing.add(
            String(
                x + box_w / 2,
                y + box_h / 2 + 3.5,
                str(index + 1),
                fontName="Helvetica-Bold",
                fontSize=7.4,
                fillColor=NAVY,
                textAnchor="middle",
            )
        )
        drawing.add(
            String(
                x + box_w / 2,
                y + box_h / 2 - 6.5,
                name,
                fontName="Helvetica",
                fontSize=6.3,
                fillColor=INK,
                textAnchor="middle",
            )
        )
    def brace(first: int, last: int, text: str, level: float, colour: HexColor) -> None:
        x1 = first * (box_w + gap_x)
        x2 = last * (box_w + gap_x) + box_w
        drawing.add(Line(x1, level, x2, level, strokeColor=colour, strokeWidth=1.2))
        drawing.add(Line(x1, level, x1, level + 4, strokeColor=colour, strokeWidth=1.2))
        drawing.add(Line(x2, level, x2, level + 4, strokeColor=colour, strokeWidth=1.2))
        drawing.add(
            String(
                x1 if first == last else (x1 + x2) / 2,
                level - 9,
                text,
                fontName="Helvetica",
                fontSize=7,
                fillColor=colour,
                textAnchor="start" if first == last else "middle",
            )
        )

    brace(0, 0, "nothing is changed", 34, BLUE)
    brace(1, 10, "every change is journaled first; a failure or a cancel restores every file", 34, GREEN)
    brace(11, 12, "kept", 34, NAVY)
    drawing.add(
        String(
            0,
            height - 9,
            "Dashed steps are the DaVinci steps. They run only when listed in [pipeline] davinci_steps.",
            fontName="Helvetica",
            fontSize=7,
            fillColor=MUTED,
        )
    )
    return drawing


# ---------------------------------------------------------------- document


class GuideDoc(BaseDocTemplate):
    """A4 document with a cover template, a body template, a contents list and bookmarks."""

    def __init__(self, filename: str) -> None:
        super().__init__(
            filename,
            pagesize=A4,
            leftMargin=MARGIN_X,
            rightMargin=MARGIN_X,
            topMargin=MARGIN_TOP,
            bottomMargin=MARGIN_BOTTOM,
            title=TITLE,
            author="PortTool project",
            subject="What PortTool does, how to use it and how to debug it",
        )
        frame = Frame(
            MARGIN_X,
            MARGIN_BOTTOM,
            FRAME_W,
            PAGE_H - MARGIN_TOP - MARGIN_BOTTOM,
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
            id="body",
        )
        self.addPageTemplates(
            [
                PageTemplate(id="cover", frames=[frame], onPage=_cover_page),
                PageTemplate(id="body", frames=[frame], onPageEnd=_body_page),
            ]
        )
        self.chapter = ""
        self._heading_number = 0

    def beforeDocument(self) -> None:
        self.chapter = ""
        self._heading_number = 0

    def afterFlowable(self, flowable: Flowable) -> None:
        if not isinstance(flowable, Paragraph):
            return
        name = flowable.style.name
        if name not in ("H1", "H2"):
            return
        text = flowable.getPlainText()
        level = 0 if name == "H1" else 1
        self._heading_number += 1
        key = f"heading-{self._heading_number}"
        self.canv.bookmarkPage(key)
        self.canv.addOutlineEntry(text, key, level=level, closed=level == 0)
        self.notify("TOCEntry", (level, text, self.page, key))
        if level == 0:
            self.chapter = text


def _cover_page(canvas, doc) -> None:  # type: ignore[no-untyped-def]
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, PAGE_H - 96 * mm, PAGE_W, 96 * mm, stroke=0, fill=1)
    canvas.setFillColor(TEAL)
    canvas.rect(0, PAGE_H - 98 * mm, PAGE_W, 2 * mm, stroke=0, fill=1)
    canvas.setFillColor(white)
    canvas.setFont("Helvetica-Bold", 34)
    canvas.drawString(MARGIN_X, PAGE_H - 52 * mm, "PortTool")
    canvas.setFont("Helvetica", 17)
    canvas.drawString(MARGIN_X, PAGE_H - 64 * mm, "Engineer Guide")
    canvas.setFont("Helvetica", 10.5)
    canvas.setFillColor(HexColor("#C9D6E2"))
    canvas.drawString(
        MARGIN_X, PAGE_H - 78 * mm, "What the tool does, how to use it, and how to debug it"
    )
    canvas.drawString(MARGIN_X, PAGE_H - 84 * mm, "Version 0.1.0  |  13 November 2026  |  Windows")
    canvas.restoreState()


def _body_page(canvas, doc) -> None:  # type: ignore[no-untyped-def]
    canvas.saveState()
    canvas.setFont("Helvetica", 7.6)
    canvas.setFillColor(MUTED)
    top = PAGE_H - 12 * mm
    canvas.drawString(MARGIN_X, top, TITLE)
    canvas.drawRightString(PAGE_W - MARGIN_X, top, doc.chapter)
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN_X, top - 3, PAGE_W - MARGIN_X, top - 3)
    canvas.drawCentredString(PAGE_W / 2, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def contents() -> TableOfContents:
    toc = TableOfContents()
    toc.levelStyles = [TOC0, TOC1]
    toc.dotsMinLevel = 0
    toc.tableStyle = TableStyle(
        [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0.6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0.6),
        ]
    )
    return toc


__all__ = [
    "FRAME_W",
    "GuideDoc",
    "NextPageTemplate",
    "PageBreak",
    "Spacer",
    "apply_diagram",
    "architecture_diagram",
    "bullets",
    "caption",
    "code",
    "contents",
    "cycle_diagram",
    "gap",
    "h1",
    "h2",
    "h3",
    "need",
    "note",
    "p",
    "picture",
    "steps",
    "table",
]
