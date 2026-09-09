"""A PDF writer with no third-party dependency, in the subset this report needs.

WHY THIS EXISTS, measured rather than assumed. The deployed Streamlit-in-Snowflake app runs
the CONTAINER runtime (`DESCRIBE STREAMLIT` reports SYSTEM$ST_CONTAINER_RUNTIME_PY3_11 on
compute pool DEV) and its resolved package set is exactly `python==3.11.*,
snowflake-snowpark-python, streamlit`. That runtime installs from `pyproject.toml` via PyPI,
which needs an External Access Integration; the app has none and the role cannot create one.
fpdf2 IS published in the Snowflake Anaconda channel - but only the WAREHOUSE runtime reads
`environment.yml`, where it was listed. So on the runtime actually deployed, the PDF button
was permanently disabled and no amount of manifest editing would have fixed it.

Everything here is standard library. zlib is used to compress content streams; it ships with
CPython, so it costs nothing.

THE SURFACE IS DELIBERATELY fpdf2's, method for method - set_font, cell, multi_cell, text,
line, rect, ellipse, polyline, get_string_width, ln, add_page, output - because the report's
layout in src/campaign_report.py was tuned against fpdf2 over several rounds, and that
tuning, not the PDF plumbing, is the part worth preserving. Coordinates are millimetres from
the top-left like fpdf2; PDF's own points-from-bottom-left is converted at the boundary.
fpdf2's cell geometry is reproduced exactly: c_margin 1mm, and a text baseline at
`y + 0.5*h + 0.3*font_size`, both read out of fpdf2's source rather than guessed.

It is NOT a general PDF library. One page size, two fonts, no images, no annotations, no
outlines. Anything the report does not do is absent on purpose.

ONE IMPROVEMENT over the fpdf2 version, taken because it was free. fpdf2 numbers pages by
writing a `{nb}` placeholder and substituting the total afterwards, which leaves a
right-aligned footer very slightly misaligned once "1" becomes "12". Here footers are not
drawn during the build at all - each page records that it wants one, and they are rendered at
output(), when the total is already known. So `total_pages` is a real number at draw time and
the alignment is exact.
"""

from __future__ import annotations

import zlib

# fpdf2's own A4, in points, so `w` and `h` come out as its 210.0016 x 297.00008 rather than a
# clean 210x297. The report's layout constants were fitted against those numbers.
A4_PT = (595.28, 841.89)
_K = 72.0 / 25.4  # points per millimetre

# Adobe's Helvetica and Helvetica-Bold advance widths, in 1/1000 em, indexed by byte value.
# Extracted from fpdf2's own CORE_FONTS_CHARWIDTHS and verified against its get_string_width
# across the report's real strings - see the suite - rather than transcribed from memory.
_W_REG = (
    278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278,
    278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278,
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
    1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
    333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
    556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584, 350,
    556, 350, 222, 556, 333, 1000, 556, 556, 333, 1000, 667, 333, 1000, 350, 611, 350,
    350, 222, 222, 333, 333, 350, 556, 1000, 333, 1000, 500, 333, 944, 350, 500, 667,
    278, 333, 556, 556, 556, 556, 260, 556, 333, 737, 370, 556, 584, 333, 737, 333,
    400, 584, 333, 333, 333, 556, 537, 278, 333, 333, 365, 556, 834, 834, 834, 611,
    667, 667, 667, 667, 667, 667, 1000, 722, 667, 667, 667, 667, 278, 278, 278, 278,
    722, 722, 778, 778, 778, 778, 778, 584, 778, 722, 722, 722, 722, 667, 667, 611,
    556, 556, 556, 556, 556, 556, 889, 500, 556, 556, 556, 556, 278, 278, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 584, 611, 556, 556, 556, 556, 500, 556, 500,
)
_W_BOLD = (
    278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278,
    278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278,
    278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611,
    975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556,
    333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611,
    611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584, 350,
    556, 350, 278, 556, 500, 1000, 556, 556, 333, 1000, 667, 333, 1000, 350, 611, 350,
    350, 278, 278, 500, 500, 350, 556, 1000, 333, 1000, 556, 333, 944, 350, 500, 667,
    278, 333, 556, 556, 556, 556, 280, 556, 333, 737, 370, 556, 584, 333, 737, 333,
    400, 584, 333, 333, 333, 611, 556, 278, 333, 333, 365, 556, 834, 834, 834, 611,
    722, 722, 722, 722, 722, 722, 1000, 722, 667, 667, 667, 667, 278, 278, 278, 278,
    722, 722, 778, 778, 778, 778, 778, 584, 778, 722, 722, 722, 722, 667, 667, 611,
    556, 556, 556, 556, 556, 556, 889, 556, 556, 556, 556, 556, 278, 278, 278, 278,
    611, 611, 611, 611, 611, 611, 611, 584, 611, 611, 611, 611, 611, 556, 611, 556,
)

# Text is written with WinAnsiEncoding and the bytes handed to it are latin-1. The two agree
# everywhere except 0x80-0x9F, which in latin-1 are C1 control codes and cannot survive the
# caller's own folding - so the mismatch is unreachable rather than tolerated.
_ESCAPE = {0x28: b"\\(", 0x29: b"\\)", 0x5C: b"\\\\"}


def _pdf_string(text: str) -> bytes:
    """A PDF literal string. Non-ASCII goes out as octal, which every reader accepts and no
    reader mistakes for a delimiter."""
    out = bytearray()
    for byte in text.encode("latin-1", "replace"):
        if byte in _ESCAPE:
            out += _ESCAPE[byte]
        elif 32 <= byte <= 126:
            out.append(byte)
        else:
            out += b"\\%03o" % byte
    return bytes(out)


def _num(value: float) -> str:
    """Two decimals is finer than a PDF point and keeps the stream small."""
    return f"{value:.2f}"


class Canvas:
    """A page canvas with fpdf2's geometry and a subset of its methods.

    `footer` is called once per page at output() with this canvas as its only argument, with
    `page_no()` and `total_pages` both already correct.
    """

    def __init__(self, footer=None, title: str = "", page_pt=A4_PT):
        self.w = page_pt[0] / _K
        self.h = page_pt[1] / _K
        self._page_pt = page_pt
        self.l_margin = 15.0
        self.r_margin = 15.0
        self.t_margin = 14.0
        self.b_margin = 20.0
        self.c_margin = 1.0
        self.auto_page_break = True
        self.total_pages = 0
        self._footer = footer
        self._title = title
        self._pages: list[bytearray] = []
        self._ops: bytearray | None = None
        self._page_index = 0
        self.x = 0.0
        self.y = 0.0
        self._style = ""
        self._size_pt = 10.0
        self._text_rgb = (0, 0, 0)
        self._fill_rgb = (0, 0, 0)
        self._draw_rgb = (0, 0, 0)
        self._line_width = 0.2
        self._last_h = 0.0
        self._in_footer = False

    # ------------------------------------------------------------------ setup
    def set_margins(self, left: float, top: float, right: float | None = None) -> None:
        """Left, top and right only - like fpdf2, the bottom margin belongs to
        set_auto_page_break, so setting margins cannot silently undo it."""
        self.l_margin = left
        self.t_margin = top
        self.r_margin = left if right is None else right

    def set_auto_page_break(self, auto: bool, margin: float = 0.0) -> None:
        self.auto_page_break = auto
        self.b_margin = margin

    def set_title(self, title: str) -> None:
        self._title = title

    @property
    def font_size(self) -> float:
        """Current size in millimetres, as fpdf2 reports it."""
        return self._size_pt / _K

    def set_font(self, family: str = "", style: str = "", size: float = 0) -> None:
        """Helvetica only - `family` is accepted and ignored so calls read like fpdf2's."""
        self._style = "B" if "B" in (style or "").upper() else ""
        if size:
            self._size_pt = float(size)

    def set_text_color(self, r: int, g: int = None, b: int = None) -> None:
        self._text_rgb = (r, r, r) if g is None else (r, g, b)

    def set_fill_color(self, r: int, g: int = None, b: int = None) -> None:
        self._fill_rgb = (r, r, r) if g is None else (r, g, b)

    def set_draw_color(self, r: int, g: int = None, b: int = None) -> None:
        self._draw_rgb = (r, r, r) if g is None else (r, g, b)

    def set_line_width(self, width: float) -> None:
        self._line_width = width

    # ------------------------------------------------------------- the cursor
    def get_x(self) -> float:
        return self.x

    def get_y(self) -> float:
        return self.y

    def set_x(self, x: float) -> None:
        self.x = x if x >= 0 else self.w + x

    def set_y(self, y: float) -> None:
        """A negative y measures up from the bottom of the page, which is how a footer places
        itself without knowing the page height."""
        self.y = y if y >= 0 else self.h + y

    def set_xy(self, x: float, y: float) -> None:
        self.set_x(x)
        self.set_y(y)

    def ln(self, h: float | None = None) -> None:
        """Carriage return. With no argument, drops by the last cell's height, like fpdf2."""
        self.x = self.l_margin
        self.y += self._last_h if h is None else h

    # -------------------------------------------------------------- the pages
    def add_page(self) -> None:
        self._ops = bytearray()
        self._pages.append(self._ops)
        self._page_index = len(self._pages)
        self.x = self.l_margin
        self.y = self.t_margin

    def page_no(self) -> int:
        return self._page_index

    def _break_if_needed(self, h: float) -> None:
        if self.auto_page_break and not self._in_footer and self.y + h > self.h - self.b_margin:
            self.add_page()

    # ----------------------------------------------------------- measurement
    def get_string_width(self, text: str) -> float:
        table = _W_BOLD if self._style == "B" else _W_REG
        total = sum(table[byte] for byte in str(text).encode("latin-1", "replace"))
        return total / 1000.0 * self._size_pt / _K

    # -------------------------------------------------------------- graphics
    def _xy(self, x: float, y: float) -> tuple[float, float]:
        """Millimetres from the top-left to points from the bottom-left."""
        return x * _K, (self.h - y) * _K

    def _paint(self, style: str) -> bytes:
        """fpdf2's style letters to a PDF path-painting operator."""
        s = (style or "D").upper()
        if s == "F":
            return b"f"
        if s in ("DF", "FD"):
            return b"B"
        return b"S"

    def _colours(self, style: str) -> bytes:
        out = bytearray()
        s = (style or "D").upper()
        if "F" in s:
            r, g, b = self._fill_rgb
            out += b"%s %s %s rg " % (_num(r / 255).encode(), _num(g / 255).encode(), _num(b / 255).encode())
        if "D" in s or s == "":
            r, g, b = self._draw_rgb
            out += b"%s %s %s RG " % (_num(r / 255).encode(), _num(g / 255).encode(), _num(b / 255).encode())
        out += b"%s w " % _num(self._line_width).encode()
        return bytes(out)

    def line(self, x1: float, y1: float, x2: float, y2: float) -> None:
        ax, ay = self._xy(x1, y1)
        bx, by = self._xy(x2, y2)
        self._ops += b"q %s%s %s m %s %s l S Q\n" % (
            self._colours("D"), _num(ax).encode(), _num(ay).encode(), _num(bx).encode(), _num(by).encode())

    def rect(self, x: float, y: float, w: float, h: float, style: str = "D") -> None:
        px, py = self._xy(x, y + h)  # PDF's rect origin is its lower-left corner
        self._ops += b"q %s%s %s %s %s re %s Q\n" % (
            self._colours(style), _num(px).encode(), _num(py).encode(),
            _num(w * _K).encode(), _num(h * _K).encode(), self._paint(style))

    def ellipse(self, x: float, y: float, w: float, h: float, style: str = "D") -> None:
        """Bounding-box positioned like fpdf2's: (x, y) is the top-left, not the centre."""
        kappa = 0.5523  # circular-arc approximation constant for a cubic Bezier
        cx, cy = x + w / 2, y + h / 2
        rx, ry = w / 2, h / 2
        ox, oy = rx * kappa, ry * kappa
        pt = lambda a, b: "%s %s" % (_num(self._xy(a, b)[0]), _num(self._xy(a, b)[1]))  # noqa: E731
        path = [
            "%s m" % pt(cx - rx, cy),
            "%s %s %s c" % (pt(cx - rx, cy - oy), pt(cx - ox, cy - ry), pt(cx, cy - ry)),
            "%s %s %s c" % (pt(cx + ox, cy - ry), pt(cx + rx, cy - oy), pt(cx + rx, cy)),
            "%s %s %s c" % (pt(cx + rx, cy + oy), pt(cx + ox, cy + ry), pt(cx, cy + ry)),
            "%s %s %s c" % (pt(cx - ox, cy + ry), pt(cx - rx, cy + oy), pt(cx - rx, cy)),
        ]
        self._ops += b"q %s%s h %s Q\n" % (
            self._colours(style), " ".join(path).encode("ascii"), self._paint(style))

    def polyline(self, points, fill: bool = False, polygon: bool = False, style: str | None = None) -> None:
        """A single path through `points`. One object in the stream, so the joins are mitred
        rather than being N separate segments with butt ends meeting at an angle."""
        if not points:
            return
        if style is None:
            style = "F" if fill else "D"
        path = ["%s %s m" % (_num(self._xy(*points[0])[0]), _num(self._xy(*points[0])[1]))]
        for px, py in points[1:]:
            ax, ay = self._xy(px, py)
            path.append("%s %s l" % (_num(ax), _num(ay)))
        if polygon:
            path.append("h")
        self._ops += b"q %s%s %s Q\n" % (
            self._colours(style), " ".join(path).encode("ascii"), self._paint(style))

    # ------------------------------------------------------------------ text
    def _draw_text(self, x: float, baseline: float, text: str) -> None:
        tx, ty = self._xy(x, baseline)
        r, g, b = self._text_rgb
        self._ops += b"q BT /%s %s Tf %s %s %s rg %s %s Td (%s) Tj ET Q\n" % (
            b"F2" if self._style == "B" else b"F1", _num(self._size_pt).encode(),
            _num(r / 255).encode(), _num(g / 255).encode(), _num(b / 255).encode(),
            _num(tx).encode(), _num(ty).encode(), _pdf_string(text))

    def text(self, x: float, y: float, text: str) -> None:
        """Text at an exact baseline, with no cell and no cursor movement."""
        self._draw_text(x, y, str(text))

    def cell(self, w: float = 0, h: float = 0, text: str = "", border=0, align: str = "",
             fill: bool = False, new_x: str = "RIGHT", new_y: str = "TOP", **kwargs) -> None:
        """One line in a box. Geometry matches fpdf2, including the 1mm side padding and the
        baseline at y + 0.5h + 0.3 * font size, so the report's spacing carries over unchanged.
        """
        text = "" if text is None else str(text)
        self._break_if_needed(h)
        if not w:
            w = self.w - self.r_margin - self.x
        if fill:
            self.rect(self.x, self.y, w, h, style="F")
        if text:
            if (align or "").upper() == "R":
                dx = w - self.c_margin - self.get_string_width(text)
            elif (align or "").upper() == "C":
                dx = (w - self.get_string_width(text)) / 2
            else:
                dx = self.c_margin
            self._draw_text(self.x + dx, self.y + 0.5 * h + 0.3 * self.font_size, text)
        self._last_h = h
        if (new_y or "").upper() == "NEXT":
            self.y += h
        if (new_x or "").upper() == "LMARGIN":
            self.x = self.l_margin
        elif (new_x or "").upper() != "LEFT":
            self.x += w

    def _wrap(self, text: str, width: float) -> list[str]:
        """Greedy word wrap, splitting inside a word only when the word alone cannot fit."""
        lines: list[str] = []
        for paragraph in str(text).split("\n"):
            line = ""
            for word in paragraph.split(" "):
                trial = f"{line} {word}" if line else word
                if line and self.get_string_width(trial) > width:
                    lines.append(line)
                    line = word
                else:
                    line = trial
                while self.get_string_width(line) > width and len(line) > 1:
                    # A single unbreakable run - a URL, or a 140-character asset name. Cut it
                    # at the last character that fits rather than letting it overprint.
                    cut = len(line)
                    while cut > 1 and self.get_string_width(line[:cut]) > width:
                        cut -= 1
                    lines.append(line[:cut])
                    line = line[cut:]
            lines.append(line)
        return lines

    def multi_cell(self, w: float = 0, h: float = 0, text: str = "", align: str = "",
                   fill: bool = False, new_x: str = "LMARGIN", new_y: str = "NEXT", **kwargs) -> None:
        """Wrapped text. Every line keeps the left edge the cell started at, so a block indented
        with set_xy stays indented - which is what the title block relies on."""
        if not w:
            w = self.w - self.r_margin - self.x
        left = self.x
        for line in self._wrap(text, w - 2 * self.c_margin):
            self._break_if_needed(h)
            self.x = left
            self.cell(w, h, line, align=align, fill=fill, new_x="LMARGIN", new_y="NEXT")
        self._last_h = h
        if (new_x or "").upper() != "LMARGIN":
            self.x = left

    # ---------------------------------------------------------------- output
    def output(self) -> bytes:
        """Serialise. Footers are drawn here, not during the build, so the page total in them
        is a real number rather than a placeholder patched in afterwards."""
        if not self._pages:
            self.add_page()
        self.total_pages = len(self._pages)
        if self._footer:
            saved = (self.x, self.y, self._style, self._size_pt, self._text_rgb, self._draw_rgb)
            self._in_footer = True
            for index, ops in enumerate(self._pages, start=1):
                self._ops = ops
                self._page_index = index
                # Reset to the left margin first, as fpdf2 does before its own footer hook, so a
                # footer only has to place itself vertically and cannot inherit an x from
                # wherever the page body happened to stop.
                self.x = self.l_margin
                self._footer(self)
            self._in_footer = False
            self.x, self.y, self._style, self._size_pt, self._text_rgb, self._draw_rgb = saved

        # Objects 1-5 are fixed; each page then contributes a page object and its stream.
        pages_obj, first_page = 2, 6
        kids = " ".join(f"{first_page + 2 * i} 0 R" for i in range(len(self._pages)))
        objects: list[bytes] = [
            b"<< /Type /Catalog /Pages %d 0 R >>" % pages_obj,
            b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids.encode("ascii"), len(self._pages)),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
            b"<< /Title (%s) /Producer (InSyte Analytics Dashboard) >>" % _pdf_string(self._title),
        ]
        box = b"[0 0 %s %s]" % (_num(self._page_pt[0]).encode(), _num(self._page_pt[1]).encode())
        for i, ops in enumerate(self._pages):
            stream = zlib.compress(bytes(ops))
            objects.append(
                b"<< /Type /Page /Parent %d 0 R /MediaBox %s /Resources << /Font << /F1 3 0 R "
                b"/F2 4 0 R >> >> /Contents %d 0 R >>" % (pages_obj, box, first_page + 2 * i + 1))
            objects.append(
                b"<< /Length %d /Filter /FlateDecode >>\nstream\n%s\nendstream" % (len(stream), stream))

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for number, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
        start_xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
        for offset in offsets:
            # Exactly 20 bytes per entry, which the cross-reference table requires.
            out += b"%010d 00000 n \n" % offset
        out += b"trailer\n<< /Size %d /Root 1 0 R /Info 5 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
            len(objects) + 1, start_xref)
        return bytes(out)
