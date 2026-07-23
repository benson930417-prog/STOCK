from __future__ import annotations

from PIL import ImageDraw

from scripts.setup_rich_menu import GAP, PAGE1, _fit_text_font, build_image


def test_page_one_text_stays_inside_each_tile() -> None:
    image = build_image(PAGE1)
    draw = ImageDraw.Draw(image)

    for _x, _y, width, _height, _icon, label, subtitle, *_rest in PAGE1:
        available = width - (2 * GAP) - 56
        label_font = _fit_text_font(
            draw, label, available, base=86, minimum=42, bold=True
        )
        subtitle_font = _fit_text_font(
            draw, subtitle, available, base=68, minimum=38, bold=False
        )
        label_box = draw.textbbox((0, 0), label, font=label_font)
        subtitle_box = draw.textbbox((0, 0), subtitle, font=subtitle_font)
        assert label_box[2] - label_box[0] <= available
        assert subtitle_box[2] - subtitle_box[0] <= available


def test_phone_row_uses_short_labels() -> None:
    labels = {(cell[5], cell[6]) for cell in PAGE1 if cell[1] == 843}
    assert ("ETF 共識", "觀察・買・賣") in labels
    assert ("那斯達克", "24 小時") in labels
