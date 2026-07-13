from core.exec_node import ExecNode


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
        condition = bool(engine.get_input_value(self, "условие"))
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

    def execute(self, engine):
        condition = bool(engine.get_input_value(self, "условие"))
        max_iterations = int(engine.get_input_value(self, "макс_итераций") or 0)
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
        seconds = float(engine.get_input_value(self, "секунды") or 0)
        engine.sleep_interruptible(seconds)
        return "выход"
