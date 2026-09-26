from NodeGraphQt import BaseNode
from NodeGraphQt.constants import NodePropWidgetEnum

from core.ports import BREAKPOINT_PROP, DATA_PORT_COLOR, EXEC_IN, EXEC_OUT, EXEC_PORT_COLOR

_WIDGET_BY_DTYPE = {
    "bool": NodePropWidgetEnum.QCHECK_BOX.value,
    # QSPIN_BOX/QDOUBLESPIN_BOX виджеты в панели свойств NodeGraphQt
    # создаются без setRange(...), поэтому у них дефолтный диапазон Qt
    # 0..99 — любое значение вне него (например, координаты области для
    # OCR) сразу же зажимается обратно в свойство. INT/FLOAT используют
    # текстовый редактор без ограничения диапазона.
    "int": NodePropWidgetEnum.INT.value,
    "float": NodePropWidgetEnum.FLOAT.value,
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
        self.create_property(
            BREAKPOINT_PROP,
            False,
            widget_type=NodePropWidgetEnum.QCHECK_BOX.value,
            widget_tooltip="Приостановить выполнение перед этой нодой (F8)",
            tab="Отладка",
        )

    def reset_runtime(self):
        """Сбрасывает состояние, накопленное за прошлый запуск (выходные
        значения, счётчики циклов). Движок зовёт это для каждой ноды в
        начале запуска — иначе остановленный посреди цикла граф при
        следующем запуске продолжил бы старый счёт. Наследники со своим
        состоянием переопределяют и вызывают ``super()``."""
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
    видели одинаковое значение. Кэш ведётся отдельно для каждой цепочки
    (Start-ноды): цепочки идут параллельно, и у каждой свой счёт шагов.
    """

    def __init__(self):
        super().__init__()
        self._cache = {}  # chain_id -> ((run_id, step_id), {порт: значение})

    def reset_runtime(self):
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
        chain_id, step_key = engine.cache_key()
        cached = self._cache.get(chain_id)
        if cached is None or cached[0] != step_key:
            cached = (step_key, self.compute(engine) or {})
            self._cache[chain_id] = cached
        return cached[1].get(name)
