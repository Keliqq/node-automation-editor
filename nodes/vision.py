import os
import time

import cv2
import numpy as np

from core.exec_node import DataNode, ExecNode
from core.ports import BOOL_PORT_COLOR
from core.region_selector import select_region
from core.screen import MONITOR_ALL, grab_monitor_bgr, grab_region_bgr, list_monitors, monitor_label, read_pixel_rgb
from core.values import to_float, to_int

_ALL_MONITORS_LABEL = "Все мониторы"

# путь -> (mtime, картинка BGR). Шаблон не перечитывается с диска на каждой
# итерации цикла, но подхватывается заново, если файл на диске изменился.
_template_cache = {}


def _imread_unicode(path):
    """cv2.imread не умеет открывать пути с не-ASCII символами на Windows
    (например, стандартное «Снимок экрана ....png» из Ножниц Windows),
    поэтому читаем байты сами и отдаём их на декодирование OpenCV."""
    if not os.path.isfile(path):
        return None
    data = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _load_template(path):
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    cached = _template_cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    image = _imread_unicode(path)
    if image is not None:
        _template_cache[path] = (mtime, image)
    return image


def _parse_region(text):
    """«x,y,ширина,высота» -> кортеж int, пустая строка -> None (весь монитор)."""
    text = str(text or "").strip()
    if not text:
        return None
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 4:
        raise ValueError(f"область должна быть «x,y,ширина,высота», а не {text!r}")
    x, y, w, h = (to_int(p) for p in parts)
    if w <= 0 or h <= 0:
        raise ValueError(f"у области нулевой размер: {text!r}")
    return x, y, w, h


def _monitor_combo_items():
    """(подписи для списка, словарь подпись -> индекс монитора) для
    выпадающего списка выбора монитора, чтобы можно было ограничить
    поиск одним экраном вместо сканирования всех сразу."""
    monitors = list_monitors()
    labels = [_ALL_MONITORS_LABEL] + [monitor_label(m) for m in monitors]
    index_by_label = {_ALL_MONITORS_LABEL: MONITOR_ALL}
    index_by_label.update({monitor_label(m): m["index"] for m in monitors})
    return labels, index_by_label


class _ImageSearchMixin:
    """Общие входы и сам поиск для «Найти картинку» и «Ждать картинку».

    Поиск по всему виртуальному рабочему столу в цикле — самое дорогое, что
    делает граф, поэтому можно сузить его до монитора или прямоугольной
    области и искать в оттенках серого (примерно втрое быстрее, но хуже
    различает картинки, отличающиеся только цветом)."""

    def _add_search_inputs(self):
        self.add_data_input("путь_к_шаблону", dtype="file", default="")
        self.add_data_input("точность", dtype="float", default=0.85)
        monitor_labels, self._monitor_index_by_label = _monitor_combo_items()
        self.add_combo_menu("монитор", "Монитор", items=monitor_labels)
        self.add_data_input(
            "область",
            dtype="str",
            default="",
            tooltip="x,y,ширина,высота — искать только здесь (пусто = весь выбранный монитор)",
        )
        self.add_button("выбрать_область", "Область:", "Выбрать на экране")
        widget = self.get_widget("выбрать_область")
        if widget is not None:
            widget.value_changed.connect(self._on_select_region)
        self.add_checkbox("серый", "Поиск в оттенках серого", state=False)

    def _on_select_region(self, *args):
        result = select_region()
        if result and len(result) == 4:
            self.set_property("область", ",".join(str(v) for v in result))
            self.view.draw_node()

    def _search(self, engine):
        """Returns: (найдено, x, y, оценка). Ошибки настройки — исключение."""
        template_path = engine.get_input_value(self, "путь_к_шаблону")
        if not template_path:
            raise ValueError("не задан путь к шаблону")
        confidence = to_float(engine.get_input_value(self, "точность"), default=0.85)

        template = _load_template(template_path)
        if template is None:
            raise ValueError(f"не удалось открыть {template_path}")

        region = _parse_region(engine.get_input_value(self, "область"))
        if region is not None:
            origin_x, origin_y = region[0], region[1]
            screen = grab_region_bgr(*region)
        else:
            monitor_label_value = self.get_property("монитор") or _ALL_MONITORS_LABEL
            monitor_index = self._monitor_index_by_label.get(monitor_label_value, MONITOR_ALL)
            screen, origin_x, origin_y = grab_monitor_bgr(monitor_index)

        h, w = template.shape[:2]
        if screen.shape[0] < h or screen.shape[1] < w:
            raise ValueError("шаблон больше области поиска")

        if self.get_property("серый"):
            screen = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
            template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

        result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val < confidence:
            return False, None, None, float(max_val)
        return True, origin_x + max_loc[0] + w // 2, origin_y + max_loc[1] + h // 2, float(max_val)


class FindImageOnScreenNode(_ImageSearchMixin, DataNode):
    """Ищет картинку-шаблон на экране через сравнение с образцом (OpenCV).

    «Чистая» нода без exec-пинов: при каждом обращении к её выходам
    делается новый скриншот и новый поиск, поэтому её можно смело
    использовать прямо как условие While Loop («повторять, пока цель не
    найдена»). Если не найдено, x/y пустые — «Клик» по ним остановит ветку,
    а не кликнет в угол экрана.
    """

    __identifier__ = "automation.vision"
    NODE_NAME = "Найти картинку на экране"

    def __init__(self):
        super().__init__()
        self._add_search_inputs()
        self.add_data_output("найдено", color=BOOL_PORT_COLOR)
        self.add_data_output("x")
        self.add_data_output("y")
        self.add_data_output("оценка")
        self.set_color(70, 115, 95)

    def compute(self, engine):
        try:
            found, x, y, score = self._search(engine)
        except ValueError as exc:
            engine.log_message.emit(f"[{self.name()}] {exc}")
            return {"найдено": False, "x": None, "y": None, "оценка": 0.0}
        return {"найдено": found, "x": x, "y": y, "оценка": score}


class WaitForImageNode(_ImageSearchMixin, ExecNode):
    """Ждёт появления картинки на экране не дольше заданного времени.

    То же, что «Цикл Пока» + «Найти картинку» + «Ждать», но одной нодой:
    выход «найдено» — картинка появилась (координаты на x/y), «таймаут» —
    так и не дождались.
    """

    __identifier__ = "automation.vision"
    NODE_NAME = "Ждать картинку"

    def __init__(self):
        super().__init__()
        self.add_exec_input()
        self.add_exec_output("найдено")
        self.add_exec_output("таймаут")
        self._add_search_inputs()
        self.add_data_input("таймаут_сек", dtype="float", default=10.0)
        self.add_data_input("интервал_сек", dtype="float", default=0.25)
        self.add_data_output("x")
        self.add_data_output("y")
        self.add_data_output("оценка")
        self.set_color(70, 115, 95)

    def execute(self, engine):
        timeout = to_float(engine.get_input_value(self, "таймаут_сек"), default=10.0)
        interval = max(0.02, to_float(engine.get_input_value(self, "интервал_сек"), default=0.25))
        deadline = time.monotonic() + max(0.0, timeout)
        score = 0.0
        while not engine.is_stopping():
            found, x, y, score = self._search(engine)
            self.set_output_value("оценка", score)
            if found:
                self.set_output_value("x", x)
                self.set_output_value("y", y)
                return "найдено"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            engine.sleep_interruptible(min(interval, remaining))
        self.set_output_value("x", None)
        self.set_output_value("y", None)
        if engine.is_stopping():
            return None
        engine.log_message.emit(f"[{self.name()}] не дождались за {timeout:g} с (лучшая оценка {score:.2f})")
        return "таймаут"


class PixelColorMatchNode(DataNode):
    """Проверяет, совпадает ли цвет пикселя экрана с заданным (с допуском)."""

    __identifier__ = "automation.vision"
    NODE_NAME = "Цвет пикселя"

    def __init__(self):
        super().__init__()
        self.add_data_input("x", dtype="int", default=0)
        self.add_data_input("y", dtype="int", default=0)
        self.add_data_input("целевой_цвет", dtype="str", default="255,255,255")
        self.add_data_input("допуск", dtype="int", default=10)
        self.add_data_output("совпадает", color=BOOL_PORT_COLOR)
        self.set_color(70, 115, 95)

    def compute(self, engine):
        color_str = engine.get_input_value(self, "целевой_цвет") or "0,0,0"
        try:
            x = to_int(engine.get_input_value(self, "x"))
            y = to_int(engine.get_input_value(self, "y"))
            tolerance = to_int(engine.get_input_value(self, "допуск"), default=0)
        except ValueError as exc:
            engine.log_message.emit(f"[Цвет пикселя] {exc}")
            return {"совпадает": False}

        parts = [p.strip() for p in str(color_str).split(",")]
        if len(parts) != 3:
            engine.log_message.emit(f"[Цвет пикселя] неверный формат цвета: {color_str}")
            return {"совпадает": False}
        try:
            target: tuple[int, int, int] = (int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError:
            engine.log_message.emit(f"[Цвет пикселя] неверный формат цвета: {color_str}")
            return {"совпадает": False}

        try:
            actual = read_pixel_rgb(x, y)
        except Exception as exc:
            engine.log_message.emit(f"[Цвет пикселя] {exc}")
            return {"совпадает": False}

        matches = all(abs(a - t) <= tolerance for a, t in zip(actual, target))
        return {"совпадает": matches}
