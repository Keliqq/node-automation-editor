from NodeGraphQt.constants import NodePropWidgetEnum

from core.exec_node import ExecNode


class StartNode(ExecNode):
    """Точка входа графа. Движок запускается от каждой найденной Start-ноды;
    несколько «Старт» на одном графе работают параллельно."""

    __identifier__ = "automation.events"
    NODE_NAME = "Старт"

    def __init__(self):
        super().__init__()
        self.add_exec_output()
        self.set_color(60, 130, 70)

    def execute(self, engine):
        engine.log_message.emit("Запуск графа...")
        return "выход"


class FunctionNode(ExecNode):
    """Начало переиспользуемой цепочки. Сама не запускается — только через
    «Вызвать функцию» с тем же именем; когда цепочка после неё доходит до
    конца, выполнение возвращается в вызвавшую ноду."""

    __identifier__ = "automation.events"
    NODE_NAME = "Функция"

    def __init__(self):
        super().__init__()
        self.add_exec_output()
        # Обычное свойство, без входного порта: имя нужно движку заранее,
        # при подготовке запуска, а не во время выполнения.
        self.create_property(
            "имя",
            "моя_функция",
            widget_type=NodePropWidgetEnum.QLINE_EDIT.value,
            widget_tooltip="Имя, по которому эту цепочку вызывает «Вызвать функцию»",
            tab="parameters",
        )
        self.set_color(60, 110, 130)

    def execute(self, engine):
        return "выход"


class CallFunctionNode(ExecNode):
    """Выполняет цепочку ноды «Функция» с указанным именем целиком и идёт
    дальше по своему выходу."""

    __identifier__ = "automation.events"
    NODE_NAME = "Вызвать функцию"

    def __init__(self):
        super().__init__()
        self.add_exec_input()
        self.add_exec_output()
        self.add_data_input("имя", dtype="str", default="моя_функция")
        self.set_color(60, 110, 130)

    def execute(self, engine):
        name = str(engine.get_input_value(self, "имя") or "").strip()
        engine.call_function(name)
        return "выход"
