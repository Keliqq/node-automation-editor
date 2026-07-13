from NodeGraphQt import BaseNode
from NodeGraphQt.constants import NodePropWidgetEnum

from core.ports import DATA_PORT_COLOR, EXEC_IN, EXEC_OUT, EXEC_PORT_COLOR

_WIDGET_BY_DTYPE = {
    "bool": NodePropWidgetEnum.QCHECK_BOX.value,
    "int": NodePropWidgetEnum.QSPIN_BOX.value,
    "float": NodePropWidgetEnum.QDOUBLESPIN_BOX.value,
    "str": NodePropWidgetEnum.QLINE_EDIT.value,
    "file": NodePropWidgetEnum.FILE_OPEN.value,
}


class ExecNode(BaseNode):
    """Базовый класс для нод, участвующих в потоке выполнения графа.

    Наследники реализуют ``execute(engine)``, который выполняет побочный
    эффект и возвращает имя exec-выхода, куда двигаться дальше (или
    ``None``, чтобы остановить эту ветку). Цикл в exec-соединениях — это и
    есть повторение (While Loop): движок просто идёт по портам дальше,
    отдельной обработки циклов не требуется.
    """

    def __init__(self):
        super().__init__()
        self._runtime_values = {}

    def add_exec_input(self, name=EXEC_IN):
        return self.add_input(name, multi_input=True, color=EXEC_PORT_COLOR)

    def add_exec_output(self, name=EXEC_OUT):
        return self.add_output(name, multi_output=False, color=EXEC_PORT_COLOR)

    def add_data_input(self, name, dtype="str", default=None, tooltip=None):
        port = self.add_input(name, multi_input=False, color=DATA_PORT_COLOR)
        self.create_property(
            name,
            default,
            widget_type=_WIDGET_BY_DTYPE.get(dtype, NodePropWidgetEnum.QLINE_EDIT.value),
            widget_tooltip=tooltip,
            tab="parameters",
        )
        return port

    def add_data_output(self, name):
        return self.add_output(name, multi_output=True, color=DATA_PORT_COLOR)

    def set_output_value(self, name, value):
        self._runtime_values[name] = value

    def get_output_value(self, name):
        return self._runtime_values.get(name)

    def execute(self, engine):
        raise NotImplementedError


class DataNode(BaseNode):
    """Базовый класс для «чистых» нод-условий без exec-пинов.

    ``compute()`` вызывается лениво — когда какая-нибудь ``ExecNode``
    читает один из выходов этой ноды, и пересчитывается не чаще одного
    раза за шаг движка, чтобы несколько чтений в рамках одного шага
    видели одинаковое значение.
    """

    def __init__(self):
        super().__init__()
        self._cache_step = -1
        self._cache = {}

    def add_data_input(self, name, dtype="str", default=None, tooltip=None):
        port = self.add_input(name, multi_input=False, color=DATA_PORT_COLOR)
        self.create_property(
            name,
            default,
            widget_type=_WIDGET_BY_DTYPE.get(dtype, NodePropWidgetEnum.QLINE_EDIT.value),
            widget_tooltip=tooltip,
            tab="parameters",
        )
        return port

    def add_data_output(self, name, color=DATA_PORT_COLOR):
        return self.add_output(name, multi_output=True, color=color)

    def compute(self, engine):
        """Возвращает словарь {имя_выходного_порта: значение}."""
        raise NotImplementedError

    def get_output_value(self, engine, name):
        if self._cache_step != engine.step_id:
            self._cache = self.compute(engine) or {}
            self._cache_step = engine.step_id
        return self._cache.get(name)
