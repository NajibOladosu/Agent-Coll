"""Render a clean code snippet image (Carbon / ray.so style).

Output: PNG bytes. Layout: gradient backdrop + rounded dark card with
syntax-highlighted code. No fake IDE chrome.
"""
import io
import os
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from pygments import lex
from pygments.lexers import get_lexer_for_filename, get_lexer_by_name, guess_lexer
from pygments.token import Token
from pygments.util import ClassNotFound


CARD_BG        = (40, 42, 54)
HEADER_DIVIDER = (68, 71, 90)
LINE_NUM       = (98, 114, 164)
FG_TEXT        = (248, 248, 242)
FG_DIM         = (139, 144, 162)

PALETTE = {
    Token.Comment:                 (98, 114, 164),
    Token.Comment.Multiline:       (98, 114, 164),
    Token.Comment.Single:          (98, 114, 164),
    Token.Keyword:                 (255, 121, 198),
    Token.Keyword.Constant:        (189, 147, 249),
    Token.Keyword.Declaration:     (255, 121, 198),
    Token.Keyword.Namespace:       (255, 121, 198),
    Token.Keyword.Pseudo:          (189, 147, 249),
    Token.Keyword.Reserved:        (255, 121, 198),
    Token.Keyword.Type:            (139, 233, 253),
    Token.Name.Builtin:            (139, 233, 253),
    Token.Name.Builtin.Pseudo:    (189, 147, 249),
    Token.Name.Class:              (139, 233, 253),
    Token.Name.Function:           (80, 250, 123),
    Token.Name.Function.Magic:     (80, 250, 123),
    Token.Name.Decorator:          (80, 250, 123),
    Token.Name.Exception:          (255, 85, 85),
    Token.Name.Variable:           (248, 248, 242),
    Token.Name.Constant:           (189, 147, 249),
    Token.Name.Tag:                (255, 121, 198),
    Token.Name.Attribute:          (80, 250, 123),
    Token.String:                  (241, 250, 140),
    Token.String.Doc:              (241, 250, 140),
    Token.String.Escape:           (255, 184, 108),
    Token.String.Interpol:         (255, 184, 108),
    Token.Number:                  (189, 147, 249),
    Token.Operator:                (255, 121, 198),
    Token.Operator.Word:           (255, 121, 198),
    Token.Punctuation:             (248, 248, 242),
    Token.Generic.Heading:         (139, 233, 253),
    Token.Generic.Inserted:        (80, 250, 123),
    Token.Generic.Deleted:         (255, 85, 85),
}


def _color_for(token_type):
    t = token_type
    while t is not None:
        if t in PALETTE:
            return PALETTE[t]
        t = t.parent
    return FG_TEXT


_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")


def _load_mono(size, italic=False):
    name = "CascadiaMonoItalic.ttf" if italic else "CascadiaMono.ttf"
    path = os.path.join(_FONT_DIR, name)
    try:
        return ImageFont.truetype(path, size=size)
    except OSError:
        for fallback in (
            "/System/Library/Fonts/Menlo.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        ):
            try:
                return ImageFont.truetype(fallback, size=size)
            except OSError:
                continue
        return ImageFont.load_default()


def _load_ui(size):
    for path in (
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _gradient_background(size, top, bottom):
    w, h = size
    bg = Image.new("RGB", size, top)
    pixels = bg.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        r = int(top[0] * (1 - t) + bottom[0] * t)
        g = int(top[1] * (1 - t) + bottom[1] * t)
        b = int(top[2] * (1 - t) + bottom[2] * t)
        for x in range(w):
            pixels[x, y] = (r, g, b)
    return bg


def _drop_shadow(card_size, radius, offset, blur, opacity):
    w, h = card_size
    pad = blur * 2 + abs(offset[1]) + 20
    shadow = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(shadow)
    d.rounded_rectangle(
        (pad, pad, pad + w - 1, pad + h - 1),
        radius=radius,
        fill=(0, 0, 0, opacity),
    )
    return shadow.filter(ImageFilter.GaussianBlur(blur)), pad


def _get_lexer(filename: str, code: str, language_hint: str | None):
    if language_hint:
        try:
            return get_lexer_by_name(language_hint, stripnl=False)
        except ClassNotFound:
            pass
    if filename:
        try:
            return get_lexer_for_filename(filename, stripnl=False)
        except ClassNotFound:
            pass
    try:
        return guess_lexer(code, stripnl=False)
    except ClassNotFound:
        return None


def _tokenize_lines(code: str, lexer):
    if lexer is None:
        return [[(Token.Text, line)] for line in code.splitlines() or [""]]
    tokens = list(lex(code, lexer))
    lines: list[list[tuple]] = [[]]
    for tok_type, tok_val in tokens:
        parts = tok_val.split("\n")
        for i, p in enumerate(parts):
            if p:
                lines[-1].append((tok_type, p))
            if i < len(parts) - 1:
                lines.append([])
    if lines and not lines[-1]:
        lines.pop()
    return lines


def _dedent(code: str) -> str:
    lines = code.splitlines()
    nonblank = [l for l in lines if l.strip()]
    if not nonblank:
        return code
    indent = min(len(l) - len(l.lstrip(" ")) for l in nonblank)
    if indent == 0:
        return code
    return "\n".join(l[indent:] if len(l) >= indent else l for l in lines)


def render_snippet(
    *,
    code: str,
    filename: str | None = None,
    language: str | None = None,
    start_line: int = 1,
    width: int = 1600,
    height: int | None = None,
    show_line_numbers: bool = True,
) -> bytes:
    code = _dedent(code.rstrip("\n"))
    code_font = _load_mono(28)
    code_font_italic = _load_mono(28, italic=True)
    ui_font = _load_ui(20)

    lexer = _get_lexer(filename or "", code, language)
    lines_tokens = _tokenize_lines(code, lexer)

    line_h = 42
    code_lines = len(lines_tokens)

    # Card layout
    card_pad_x = 40
    card_pad_top_traffic = 56
    has_filename = bool(filename)
    header_h = card_pad_top_traffic + (28 if has_filename else 8)
    code_block_h = code_lines * line_h + 32
    card_inner_h = header_h + code_block_h + 40
    card_w = width - 200  # outer padding
    card_h = card_inner_h
    card_radius = 24

    if height is None:
        height = card_h + 200  # outer padding top + bottom

    # Background gradient
    img = _gradient_background((width, height), (61, 90, 254), (255, 95, 130))

    # Card
    card_x = (width - card_w) // 2
    card_y = (height - card_h) // 2

    # Drop shadow
    shadow, shadow_pad = _drop_shadow(
        (card_w, card_h), card_radius, offset=(0, 24), blur=40, opacity=140
    )
    img.paste(
        shadow,
        (card_x - shadow_pad, card_y - shadow_pad + 18),
        shadow,
    )

    # Card body w/ rounded corners
    card_layer = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    card_draw = ImageDraw.Draw(card_layer)
    card_draw.rounded_rectangle(
        (0, 0, card_w - 1, card_h - 1),
        radius=card_radius,
        fill=CARD_BG + (255,),
    )

    # Traffic lights
    tl_y = 30
    tl_x = 28
    for i, color in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        cx = tl_x + i * 26
        card_draw.ellipse((cx, tl_y, cx + 14, tl_y + 14), fill=color)

    # Filename centered
    if has_filename:
        fn_text = filename
        fw = int(card_draw.textlength(fn_text, font=ui_font))
        card_draw.text(
            ((card_w - fw) // 2, tl_y - 2),
            fn_text,
            font=ui_font,
            fill=FG_DIM,
        )

    # Divider
    card_draw.line(
        (24, header_h - 2, card_w - 24, header_h - 2),
        fill=HEADER_DIVIDER,
        width=1,
    )

    # Code block
    code_y0 = header_h + 16
    code_x0 = card_pad_x

    if show_line_numbers:
        max_ln_chars = len(str(start_line + code_lines - 1))
        # measure gutter width using a char of digit width
        digit_w = int(card_draw.textlength("0", font=code_font))
        gutter_w = digit_w * max_ln_chars + 24
    else:
        gutter_w = 0

    char_w = max(int(card_draw.textlength("M", font=code_font)), 1)
    available_chars = (card_w - code_x0 * 2 - gutter_w) // char_w

    for i, toks in enumerate(lines_tokens):
        ly = code_y0 + i * line_h

        if show_line_numbers:
            ln_str = str(start_line + i).rjust(max_ln_chars)
            card_draw.text(
                (code_x0, ly),
                ln_str,
                font=code_font,
                fill=LINE_NUM,
            )

        cx = code_x0 + gutter_w
        char_count = 0
        for tok_type, tok_val in toks:
            if char_count >= available_chars:
                break
            remaining = available_chars - char_count
            piece = tok_val if len(tok_val) <= remaining else tok_val[: remaining - 1] + "…"
            color = _color_for(tok_type)
            f = code_font_italic if tok_type in (
                Token.Comment, Token.Comment.Single, Token.Comment.Multiline
            ) else code_font
            card_draw.text((cx, ly), piece, font=f, fill=color)
            cx += int(card_draw.textlength(piece, font=f))
            char_count += len(piece)

    img.paste(card_layer, (card_x, card_y), card_layer)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
