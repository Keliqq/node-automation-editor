import os

import cv2
import numpy as np

from core.exec_node import DataNode
from core.ports import BOOL_PORT_COLOR
from core.screen import MONITOR_ALL, grab_monitor_bgr, list_monitors, monitor_label, read_pixel_rgb

_ALL_MONITORS_LABEL = "Все мониторы"


def _imread_unicode(path):
    """cv2.imread не умеет открывать пути с не-ASCII символами на Windows
    (например, стандартное «Снимок экрана ....png» из Ножниц Windows),
    поэтому читаем байты сами и отдаём их на декодирование OpenCV."""
    if not os.path.isfile(path):
        return None
    data = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _monitor_combo_items():
    """(подписи для списка, словарь подпись -> индекс монитора) для
    выпадающего списка выбора монитора, чтобы можно было ограничить
    поиск одним экраном вместо сканирования всех сразу."""
    monitors = list_monitors()
    labels = [_ALL_MONITORS_LABEL] + [monitor_label(m) for m in monitors]
    index_by_label = {_ALL_MONITORS_LABEL: MONITOR_ALL}
    index_by_label.update({monitor_label(m): m["index"] for m in monitors})
    return labels, index_by_label


class FindImageOnScreenNode(DataNode):
    """Ищет картинку-шаблон на экране через сравнение с образцом (OpenCV).

    «Чистая» нода без exec-пинов: при каждом обращении к её выходам
    делается новый скриншот и новый поиск, поэтому её можно смело
    использовать прямо как условие While Loop («повторять, пока цель не
    найдена»).
    """

    __identifier__ = "automation.vision"
    NODE_NAME = "Найти картинку на экране"

    def __init__(self):
        super().__init__()
        self.add_data_input("путь_к_шаблону", dtype="file", default="")
        self.add_data_input("точность", dtype="float", default=0.85)
        monitor_labels, self._monitor_index_by_label = _monitor_combo_items()
        self.add_combo_menu("монитор", "Монитор", items=monitor_labels)
        self.add_data_output("найдено", color=BOOL_PORT_COLOR)
        self.add_data_output("x")
        self.add_data_output("y")
        self.add_data_output("оценка")
        self.set_color(70, 115, 95)

    def compute(self, engine):
        template_path = engine.get_input_value(self, "путь_к_шаблону")
        confidence = float(engine.get_input_value(self, "точность") or 0.8)
        empty = {"найдено": False, "x": None, "y": None, "оценка": 0.0}

        if not template_path:
            return empty

        template = _imread_unicode(template_path)
        if template is None:
            engine.log_message.emit(f"[Найти картинку] не удалось открыть {template_path}")
            return empty

        monitor_label_value = self.get_property("монитор") or _ALL_MONITORS_LABEL
        monitor_index = self._monitor_index_by_label.get(monitor_label_value, MONITOR_ALL)
        screen, origin_x, origin_y = grab_monitor_bgr(monitor_index)

        result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val < confidence:
            return {"найдено": False, "x": None, "y": None, "оценка": float(max_val)}

        h, w = template.shape[:2]
        center_x = origin_x + max_loc[0] + w // 2
        center_y = origin_y + max_loc[1] + h // 2
        return {"найдено": True, "x": center_x, "y": center_y, "оценка": float(max_val)}


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
        x = int(engine.get_input_value(self, "x") or 0)
        y = int(engine.get_input_value(self, "y") or 0)
        color_str = engine.get_input_value(self, "целевой_цвет") or "0,0,0"
        tolerance = int(engine.get_input_value(self, "допуск") or 0)

        parts = [p.strip() for p in color_str.split(",")]
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
