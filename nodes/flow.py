from core.exec_node import DataNode, ExecNode
from core.ports import BOOL_PORT_COLOR
from core.values import to_bool, to_float, to_int


class IfElseNode(ExecNode):
    """Направляет поток по одной из двух веток в зависимости от условия."""

    __identifier__ = "automation.flow"
    NODE_NAME = "Если / Иначе"

    def __init__(self):
        super().__init__()
        self.add_exec_input()
        self.add_exec_output("истина")
        self.add_exec_output("ложь")
        self.add_data_input("условие", dtype="bool", default=True)
        self.set_color(95, 95, 140)

    def execute(self, engine):
        condition = to_bool(engine.get_input_value(self, "условие"))
        return "истина" if condition else "ложь"


class WhileLoopNode(ExecNode):
    """Повторяет ветку ``тело_цикла``, пока выполняется условие.

    Повторение — это просто цикл в exec-проводке: соедини конец тела
    цикла обратно со входом этой ноды. Если условие не подключено, оно
    считается истинным по умолчанию, а ``макс_итераций`` работает как
    счётчик «повторить N раз».
    """

    __identifier__ = "automation.flow"
    NODE_NAME = "Цикл Пока"

    def __init__(self):
        super().__init__()
        self.add_exec_input()
        self.add_exec_output("тело_цикла")
        self.add_exec_output("завершено")
        self.add_data_input("условие", dtype="bool", default=True)
        self.add_data_input("макс_итераций", dtype="int", default=1000)
        self._iterations = 0
        self.set_color(95, 95, 140)

    def reset_runtime(self):
        super().reset_runtime()
        self._iterations = 0

    def execute(self, engine):
        condition = to_bool(engine.get_input_value(self, "условие"))
        max_iterations = to_int(engine.get_input_value(self, "макс_итераций"), default=0)
        self._iterations += 1
        if condition and self._iterations <= max_iterations:
            return "тело_цикла"
        self._iterations = 0
        return "завершено"


class WaitNode(ExecNode):
    """Приостанавливает поток на заданное число секунд, прерывается «Стоп»."""

    __identifier__ = "automation.flow"
    NODE_NAME = "Ждать"

    def __init__(self):
        super().__init__()
        self.add_exec_input()
        self.add_exec_output()
        self.add_data_input("секунды", dtype="float", default=1.0)
        self.set_color(95, 95, 140)

    def execute(self, engine):
        seconds = to_float(engine.get_input_value(self, "секунды"), default=0)
        engine.sleep_interruptible(seconds)
        return "выход"


class CompareNode(DataNode):
    """Сравнивает два числа через выбранный оператор, результат — bool."""

    __identifier__ = "automation.flow"
    NODE_NAME = "Сравнить числа"

    def __init__(self):
        super().__init__()
        self.add_data_input("значение", dtype="float", default=0)
        self.add_data_input("с_чем", dtype="float", default=0)
        self.add_combo_menu("операция", "Операция:", items=[">", "<", ">=", "<=", "==", "!="])
        self.add_data_output("результат", color=BOOL_PORT_COLOR)
        self.set_color(95, 95, 140)

    _OPS = {
        ">": lambda a, b: a > b,
        "<": lambda a, b: a < b,
        ">=": lambda a, b: a >= b,
        "<=": lambda a, b: a <= b,
        "==": lambda a, b: a == b,
        "!=": lambda a, b: a != b,
    }

    def compute(self, engine):
        value = engine.get_input_value(self, "значение")
        other = engine.get_input_value(self, "с_чем")
        try:
            value, other = to_float(value), to_float(other)
        except ValueError as exc:
            # «нет значения» (OCR ничего не прочитал) — это не 0, поэтому
            # сравнение просто ложно, а не «0 < 100 — истина».
            engine.log_message.emit(f"[{self.name()}] сравнение пропущено: {exc}")
            return {"результат": False}
        op = self.get_property("операция") or ">"
        return {"результат": self._OPS.get(op, self._OPS[">"])(value, other)}
