import re

import pytesseract

from core.exec_node import DataNode
from core.ocr import read_text
from core.screen import grab_region_rgb

_NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def _parse_number(text):
    match = _NUMBER_RE.search(text)
    if not match:
        return None
    return float(match.group().replace(",", "."))


class ReadTextFromScreenNode(DataNode):
    """Читает текст/числа из заданной области экрана через Tesseract OCR.

    Для значений HUD (HP, монеты, таймеры, ...): задай x/y/ширину_области/
    высоту_области, включи «Только цифры» для счётчиков, а дальше
    используй ``число`` (например, подключи в условие If/Else).
    """

    __identifier__ = "automation.vision"
    NODE_NAME = "Прочитать текст с экрана"

    def __init__(self):
        super().__init__()
        self.add_data_input("x", dtype="int", default=0)
        self.add_data_input("y", dtype="int", default=0)
        self.add_data_input("ширина_области", dtype="int", default=100)
        self.add_data_input("высота_области", dtype="int", default=40)
        self.add_checkbox("только_цифры", "Только цифры", state=True)
        self.add_data_output("текст")
        self.add_data_output("число")
        self.set_color(70, 115, 95)

    def compute(self, engine):
        x = int(engine.get_input_value(self, "x") or 0)
        y = int(engine.get_input_value(self, "y") or 0)
        width = int(engine.get_input_value(self, "ширина_области") or 0)
        height = int(engine.get_input_value(self, "высота_области") or 0)
        digits_only = bool(self.get_property("только_цифры"))
        empty = {"текст": "", "число": None}

        if width <= 0 or height <= 0:
            return empty

        region = grab_region_rgb(x, y, width, height)
        try:
            text = read_text(region, digits_only=digits_only)
        except pytesseract.TesseractNotFoundError:
            engine.log_message.emit(
                "[OCR] Tesseract не найден. Укажи путь к tesseract.exe "
                "в меню «Настройки» → «Путь к Tesseract OCR...»."
            )
            return empty
        except Exception as exc:
            engine.log_message.emit(f"[OCR] {exc}")
            return empty

        return {"текст": text, "число": _parse_number(text)}
