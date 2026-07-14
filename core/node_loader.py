"""Загрузка пользовательских нод из папки ``user_nodes/``.

Любой ``.py`` файл там (кроме начинающихся с ``_``) может определить один
или несколько классов-наследников ``ExecNode``/``DataNode`` — они
подхватываются автоматически, без изменений в остальном коде. Сломанный
файл пользователя не должен ронять всё приложение, поэтому каждый файл
импортируется отдельно и его ошибка просто логируется и пропускается.
"""

import importlib.util
import os

from core.app_paths import APP_ROOT
from core.exec_node import DataNode, ExecNode

USER_NODES_DIR = os.path.join(APP_ROOT, "user_nodes")

_BASE_CLASSES = (ExecNode, DataNode)


def discover_user_nodes(log=None):
    """Импортирует все файлы из ``user_nodes/`` и возвращает список найденных
    в них классов-нод.

    Args:
        log (Callable[[str], None] | None): куда сообщать об ошибках/пропусках
            (например, в лог-панель приложения); если не задан — только print.

    Returns:
        list[type]: классы-ноды, готовые для ``NodeGraph.register_nodes``.
    """

    def _log(message):
        print(f"[user_nodes] {message}")
        if log:
            log(message)

    found = []
    if not os.path.isdir(USER_NODES_DIR):
        return found

    for filename in sorted(os.listdir(USER_NODES_DIR)):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue

        path = os.path.join(USER_NODES_DIR, filename)
        module_name = f"user_nodes.{filename[:-3]}"

        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            _log(f"{filename}: не удалось создать спецификацию модуля")
            continue

        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            _log(f"{filename}: ошибка загрузки — {exc}")
            continue

        for name, value in vars(module).items():
            if not (isinstance(value, type) and issubclass(value, _BASE_CLASSES)):
                continue
            if value in _BASE_CLASSES or value.__module__ != module_name:
                continue  # сама база или класс, просто заимпортированный в файл, а не объявленный в нём
            if not getattr(value, "__identifier__", None) or not getattr(value, "NODE_NAME", None):
                _log(f"{filename}: класс {name} пропущен — не задан __identifier__ или NODE_NAME")
                continue
            found.append(value)

    return found
