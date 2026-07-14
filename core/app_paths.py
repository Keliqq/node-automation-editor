"""Корневая директория приложения.

При обычном запуске из исходников это корень репозитория. После сборки
PyInstaller'ом — папка, где лежит сам ``.exe``, а не временная папка
распаковки (``sys._MEIPASS``): та создаётся заново при каждом запуске и
стирается при закрытии, так что класть туда ``examples/``/``user_nodes/``
бессмысленно — пользователь не найдёт их между запусками и не сможет
редактировать. ``sys.executable`` в frozen-режиме указывает на реальный,
постоянный путь к exe, поэтому от него и пляшем.
"""

import os
import sys


def get_app_root():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


APP_ROOT = get_app_root()
