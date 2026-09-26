"""Захват экрана с несколькими мониторами на основе ``mss``.

``mss`` сама перечисляет физические мониторы и сообщает позицию каждого в
абсолютных координатах виртуального рабочего стола (которые могут быть
отрицательными — например, если монитор стоит выше/левее основного), так
что ноды могут дать пользователю выбрать конкретный монитор для поиска
вместо того, чтобы всегда сканировать все экраны сразу.
"""

import cv2
import numpy as np
from mss import MSS

MONITOR_ALL = 0


def list_monitors():
    """Только физические мониторы (без индекса 0 — служебной записи mss
    «все мониторы вместе»).

    Returns:
        list[dict]: каждый с ``index`` (годится как ``monitor_index`` ниже),
            ``left``, ``top``, ``width``, ``height``.
    """
    with MSS() as sct:
        return [
            {"index": i, "left": m["left"], "top": m["top"], "width": m["width"], "height": m["height"]}
            for i, m in enumerate(sct.monitors[1:], start=1)
        ]


def monitor_label(monitor):
    return f"Монитор {monitor['index']} ({monitor['width']}x{monitor['height']})"


def grab_monitor_bgr(monitor_index=MONITOR_ALL):
    """Скриншот одного монитора (или всех сразу, если 0), как BGR-массив numpy.

    Returns:
        tuple[np.ndarray, int, int]: изображение и смещение (left, top) его
            левого верхнего угла в абсолютных координатах экрана.
    """
    with MSS() as sct:
        monitor = sct.monitors[monitor_index]
        shot = sct.grab(monitor)
    frame = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
    return frame, monitor["left"], monitor["top"]


def grab_region_rgb(x, y, width, height):
    """Скриншот области в абсолютных координатах экрана, как RGB-массив
    numpy. Работает и через границу мониторов, так как не привязан к
    захвату какого-то одного из них."""
    with MSS() as sct:
        shot = sct.grab({"left": x, "top": y, "width": width, "height": height})
    return cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2RGB)


def read_pixel_rgb(x, y):
    """Цвет одного пикселя экрана в абсолютных координатах, как (r, g, b)."""
    with MSS() as sct:
        shot = sct.grab({"left": x, "top": y, "width": 1, "height": 1})
    return shot.pixel(0, 0)


def grab_region_bgr(x, y, width, height):
    """Как ``grab_region_rgb``, но в BGR — для OpenCV-поиска по шаблону."""
    with MSS() as sct:
        shot = sct.grab({"left": x, "top": y, "width": width, "height": height})
    return cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
