from core.exec_node import DataNode, ExecNode
from core.values import to_float


class SetVariableNode(ExecNode):
    """Записывает значение в переменную. Переменные живут в пределах одного
    запуска и общие для всех цепочек «Старт» и функций."""

    __identifier__ = "automation.variables"
    NODE_NAME = "Задать переменную"

    def __init__(self):
        super().__init__()
        self.add_exec_input()
        self.add_exec_output()
        self.add_data_input("имя", dtype="str", default="счётчик")
        self.add_data_input("значение", dtype="str", default="0")
        self.set_color(110, 80, 130)

    def execute(self, engine):
        name = str(engine.get_input_value(self, "имя") or "").strip()
        if not name:
            raise ValueError("не задано имя переменной")
        engine.set_variable(name, engine.get_input_value(self, "значение"))
        return "выход"


class IncrementVariableNode(ExecNode):
    """Прибавляет к числовой переменной шаг (отрицательный — вычитает).
    Несуществующая переменная считается нулём — удобно как счётчик."""

    __identifier__ = "automation.variables"
    NODE_NAME = "Увеличить переменную"

    def __init__(self):
        super().__init__()
        self.add_exec_input()
        self.add_exec_output()
        self.add_data_input("имя", dtype="str", default="счётчик")
        self.add_data_input("шаг", dtype="float", default=1)
        self.add_data_output("значение")
        self.set_color(110, 80, 130)

    def execute(self, engine):
        name = str(engine.get_input_value(self, "имя") or "").strip()
        if not name:
            raise ValueError("не задано имя переменной")
        step = to_float(engine.get_input_value(self, "шаг"), default=1)

        def add(current):
            result = to_float(current, default=0) + step
            return int(result) if result.is_integer() else result

        self.set_output_value("значение", engine.update_variable(name, add))
        return "выход"


class GetVariableNode(DataNode):
    """Читает переменную. Если её ещё не задавали — «значение_по_умолчанию»."""

    __identifier__ = "automation.variables"
    NODE_NAME = "Получить переменную"

    def __init__(self):
        super().__init__()
        self.add_data_input("имя", dtype="str", default="счётчик")
        self.add_data_input("значение_по_умолчанию", dtype="str", default="")
        self.add_data_output("значение")
        self.set_color(110, 80, 130)

    def compute(self, engine):
        name = str(engine.get_input_value(self, "имя") or "").strip()
        default = engine.get_input_value(self, "значение_по_умолчанию")
        return {"значение": engine.get_variable(name, default)}
