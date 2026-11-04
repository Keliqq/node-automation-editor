import pyautogui

from core.exec_node import ExecNode
from core.values import MissingValueError, to_float, to_int

_BUTTON_BY_LABEL = {"Левая": "left", "Правая": "right", "Средняя": "middle"}


def _read_point(engine, node):
    """Координаты x/y как int. Пустое значение — ошибка, а не (0, 0): обычно
    это «Найти картинку», которая ничего не нашла, и клик в угол экрана
    вместо остановки ветки был бы худшим из вариантов."""
    try:
        return (
            to_int(engine.get_input_value(node, "x")),
            to_int(engine.get_input_value(node, "y")),
        )
    except MissingValueError:
        raise MissingValueError("координаты не заданы (картинка не найдена?) — ветка остановлена") from None


class ClickNode(ExecNode):
    """Кликает мышью в точку x, y экрана (например, по координатам из «Ждать картинку»)."""

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
        x, y = _read_point(engine, self)
        button = _BUTTON_BY_LABEL.get(str(self.get_property("кнопка")), "left")
        pyautogui.click(x=x, y=y, button=button)
        engine.log_message.emit(f"Клик ({x}, {y}) [{button}]")
        return "выход"


class MoveMouseNode(ExecNode):
    """Плавно переводит курсор в точку x, y экрана за «длительность» секунд, не кликая."""

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
        x, y = _read_point(engine, self)
        duration = to_float(engine.get_input_value(self, "длительность"), default=0)
        pyautogui.moveTo(x, y, duration=duration)
        return "выход"


class KeyPressNode(ExecNode):
    """Нажимает и отпускает одну клавишу (имена pyautogui: space, enter, esc, a, f5...); сочетания не поддерживает."""

    __identifier__ = "automation.actions"
    NODE_NAME = "Нажать клавишу"

    def __init__(self):
        super().__init__()
        self.add_exec_input()
        self.add_exec_output()
        self.add_data_input("клавиша", dtype="str", default="space")
        self.set_color(140, 95, 60)

    def execute(self, engine):
        key = str(engine.get_input_value(self, "клавиша") or "").strip()
        if key:
            pyautogui.press(key)
            engine.log_message.emit(f"Клавиша: {key}")
        return "выход"


class TypeTextNode(ExecNode):
    """Печатает текст в активное окно, как с клавиатуры (кириллица тоже работает).

    Через ``keyboard.write``, а не ``pyautogui.typewrite``:
    последний умеет только ASCII и молча пропускает кириллицу, а
    ``keyboard`` шлёт символы вне текущей раскладки как Unicode-ввод."""

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
        import keyboard

        value = engine.get_input_value(self, "текст")
        text = "" if value is None else str(value)
        interval = to_float(engine.get_input_value(self, "интервал"), default=0)
        for char in text:
            if engine.is_stopping():
                break
            keyboard.write(char)
            if interval > 0:
                engine.sleep_interruptible(interval)
        return "выход"
