"""Пример пользовательской ноды — шаблон для тех, кто хочет добавить свою.

Как добавить ноду:
1. Положи .py файл сюда, в user_nodes/ (имя файла не должно начинаться с "_").
2. Объяви класс — наследник ExecNode (для нод с exec-входом/выходом, которые
   выполняют действие) или DataNode (для «чистых» нод, которые просто
   вычисляют значение по запросу, без exec-пинов — см. nodes/vision.py,
   nodes/ocr.py).
3. Обязательно задай __identifier__ (категория, определяет папку в панели
   нод слева) и NODE_NAME (подпись на самой ноде).
4. Нода появится в приложении после «Добавить узел» → «Обновить список нод»
   (или просто при следующем запуске).
"""

import winsound

from core.exec_node import ExecNode
from core.values import to_int


class BeepNode(ExecNode):
    __identifier__ = "automation.actions"
    NODE_NAME = "Пример: Бип"

    def __init__(self):
        super().__init__()
        self.add_exec_input()
        self.add_exec_output()
        self.add_data_input("частота_гц", dtype="int", default=750)
        self.add_data_input("длительность_мс", dtype="int", default=200)
        self.set_color(140, 95, 60)

    def execute(self, engine):
        frequency = to_int(engine.get_input_value(self, "частота_гц"), default=750)
        duration = to_int(engine.get_input_value(self, "длительность_мс"), default=200)
        winsound.Beep(max(37, min(frequency, 32767)), max(1, duration))
        engine.log_message.emit(f"Бип {frequency} Гц, {duration} мс")
        return "выход"
