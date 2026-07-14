import pyautogui

from core.exec_node import ExecNode

_BUTTON_BY_LABEL = {"Левая": "left", "Правая": "right", "Средняя": "middle"}


class ClickNode(ExecNode):
    __identifier__ = "automation.actions"
    NODE_NAME = "Клик"

    def __init__(self):
        super().__init__()
        self.add_exec_input()
        self.add_exec_output()
        self.add_data_input("x", dtype="int", default=0)
        self.add_data_input("y", dtype="int", default=0)
        self.add_combo_menu("кнопка", "Кнопка", items=list(_BUTTON_BY_LABEL))
        self.set_color(140, 95, 60)

    def execute(self, engine):
        x = int(engine.get_input_value(self, "x") or 0)
        y = int(engine.get_input_value(self, "y") or 0)
        button = _BUTTON_BY_LABEL.get(str(self.get_property("кнопка")), "left")
        pyautogui.click(x=x, y=y, button=button)
        engine.log_message.emit(f"Клик ({x}, {y}) [{button}]")
        return "выход"


class MoveMouseNode(ExecNode):
    __identifier__ = "automation.actions"
    NODE_NAME = "Переместить мышь"

    def __init__(self):
        super().__init__()
        self.add_exec_input()
        self.add_exec_output()
        self.add_data_input("x", dtype="int", default=0)
        self.add_data_input("y", dtype="int", default=0)
        self.add_data_input("длительность", dtype="float", default=0.2)
        self.set_color(140, 95, 60)

    def execute(self, engine):
        x = int(engine.get_input_value(self, "x") or 0)
        y = int(engine.get_input_value(self, "y") or 0)
        duration = float(engine.get_input_value(self, "длительность") or 0)
        pyautogui.moveTo(x, y, duration=duration)
        return "выход"


class KeyPressNode(ExecNode):
    __identifier__ = "automation.actions"
    NODE_NAME = "Нажать клавишу"

    def __init__(self):
        super().__init__()
        self.add_exec_input()
        self.add_exec_output()
        self.add_data_input("клавиша", dtype="str", default="space")
        self.set_color(140, 95, 60)

    def execute(self, engine):
        key = engine.get_input_value(self, "клавиша") or ""
        if key:
            pyautogui.press(key)
            engine.log_message.emit(f"Клавиша: {key}")
        return "выход"


class TypeTextNode(ExecNode):
    __identifier__ = "automation.actions"
    NODE_NAME = "Ввести текст"

    def __init__(self):
        super().__init__()
        self.add_exec_input()
        self.add_exec_output()
        self.add_data_input("текст", dtype="str", default="")
        self.add_data_input("интервал", dtype="float", default=0.02)
        self.set_color(140, 95, 60)

    def execute(self, engine):
        text = engine.get_input_value(self, "текст") or ""
        interval = float(engine.get_input_value(self, "интервал") or 0)
        pyautogui.typewrite(text, interval=interval)
        return "выход"
