import re

import pytesseract

from core.exec_node import DataNode
from core.ocr import read_text
from core.region_selector import select_region
from core.screen import grab_region_rgb
from core.values import to_bool, to_int

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
        self.add_button("выбрать_область", "Область:", "Выбрать на экране")
        widget = self.get_widget("выбрать_область")
        if widget is not None:
            widget.value_changed.connect(self._on_select_region)
        self.add_data_output("текст")
        self.add_data_output("число")
        self.set_color(70, 115, 95)

    def _on_select_region(self, *args):
        result = select_region()
        if result and len(result) == 4:
            x_val, y_val, w, h = result
            for name, val in [("x", x_val), ("y", y_val), ("ширина_области", w), ("высота_области", h)]:
                if self.get_property(name) != val:
                    self.set_property(name, val)
            self.view.draw_node()

    def compute(self, engine):
        # Через engine, а не get_property — иначе провод, подключённый к x/y,
        # молча игнорировался бы в пользу литерала в «Свойствах».
        x_val = to_int(engine.get_input_value(self, "x"), default=0)
        y_val = to_int(engine.get_input_value(self, "y"), default=0)
        width = to_int(engine.get_input_value(self, "ширина_области"), default=0)
        height = to_int(engine.get_input_value(self, "высота_области"), default=0)
        digits_only = to_bool(self.get_property("только_цифры"))
        empty = {"текст": "", "число": None}

        if width <= 0 or height <= 0:
            return empty

        region = grab_region_rgb(x_val, y_val, width, height)
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
