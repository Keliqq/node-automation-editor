import threading
import traceback

from PySide6 import QtCore

from core.exec_node import DataNode, ExecNode
from core.ports import BREAKPOINT_PROP

MAX_CALL_DEPTH = 64


class GraphEngine(QtCore.QThread):
    """Проходит по exec-графу в фоновом потоке.

    Работает вне GUI-потока, чтобы долгий цикл автоматизации не замораживал
    редактор, и чтобы «Остановить» срабатывало сразу между шагами, а не
    только после завершения всего графа.

    Движок одноразовый: создаётся на каждый запуск, и в конструкторе (то
    есть ещё в GUI-потоке) снимает копию соединений графа. Дальше фоновый
    поток ходит только по этой копии, а не по живым портам NodeGraphQt —
    пользователь может двигать, соединять и удалять ноды во время прогона,
    не рискуя гонкой с движком (новые провода вступят в силу со следующего
    запуска). Значения свойств при этом читаются вживую, так что поправить
    число в «Свойствах» можно прямо на ходу.

    Каждая Start-нода — своя цепочка в своём потоке, цепочки идут
    параллельно (бесконечный цикл в одной не блокирует остальные) и делят
    между собой только переменные.
    """

    node_started = QtCore.Signal(str)
    node_finished = QtCore.Signal(str)
    log_message = QtCore.Signal(str)
    paused = QtCore.Signal(str)
    resumed = QtCore.Signal()
    run_finished = QtCore.Signal()

    _run_counter = 0

    def __init__(self, graph, parent=None, start_paused=False):
        super().__init__(parent)
        self.graph = graph
        GraphEngine._run_counter += 1
        self.run_id = GraphEngine._run_counter
        self._stop_event = threading.Event()
        self._local = threading.local()

        self._pause_cond = threading.Condition()
        self._paused = start_paused
        self._step_budget = 0

        self._variables = {}
        self._variables_lock = threading.Lock()

        self._take_snapshot()

    # ---- снимок графа (вызывается в GUI-потоке) ----
    def _take_snapshot(self):
        from nodes.events import FunctionNode, StartNode

        self._nodes = list(self.graph.all_nodes())
        self._exec_next = {}  # (id ноды, выходной порт) -> следующая нода
        self._data_src = {}  # (id ноды, входной порт) -> (нода-источник, её порт)
        for node in self._nodes:
            for name, port in node.outputs().items():
                connected = port.connected_ports()
                if connected:
                    self._exec_next[(node.id, name)] = connected[0].node()
            for name, port in node.inputs().items():
                connected = port.connected_ports()
                if connected:
                    self._data_src[(node.id, name)] = (connected[0].node(), connected[0].name())

        self._start_nodes = [n for n in self._nodes if isinstance(n, StartNode)]
        self._snapshot_warnings = []  # сигналы ещё не подключены — отдадим в лог при старте
        self._functions = {}
        for node in self._nodes:
            if isinstance(node, FunctionNode):
                name = str(node.get_property("имя") or "").strip()
                if name in self._functions:
                    self._snapshot_warnings.append(f"Функция «{name}» объявлена несколько раз — используется первая.")
                    continue
                self._functions[name] = node

    # ---- управление из GUI-потока ----
    def stop(self):
        self._stop_event.set()
        with self._pause_cond:
            self._pause_cond.notify_all()

    def is_stopping(self):
        return self._stop_event.is_set()

    def pause(self):
        with self._pause_cond:
            self._paused = True

    def resume(self):
        with self._pause_cond:
            self._paused = False
            self._step_budget = 0
            self._pause_cond.notify_all()

    def step(self):
        """Выполнить одну ноду и снова встать на паузу."""
        with self._pause_cond:
            self._paused = True
            self._step_budget += 1
            self._pause_cond.notify_all()

    def is_paused(self):
        return self._paused

    # ---- выполнение (фоновый поток) ----
    def run(self):
        # _stop_event здесь не сбрасывается: движок одноразовый, а «Стоп»,
        # нажатый сразу после запуска (до старта потока), не должен теряться.
        for node in self._nodes:
            if isinstance(node, (ExecNode, DataNode)):
                node.reset_runtime()
        for warning in self._snapshot_warnings:
            self.log_message.emit(warning)

        if not self._start_nodes:
            self.log_message.emit("Нет узла «Старт» на графе — добавьте его, чтобы запустить выполнение.")
            self.run_finished.emit()
            return

        if len(self._start_nodes) == 1:
            self._run_start(self._start_nodes[0], 0)
        else:
            threads = [
                threading.Thread(target=self._run_start, args=(start, index), daemon=True)
                for index, start in enumerate(self._start_nodes)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.run_finished.emit()

    def _run_start(self, start, chain_id):
        self._local.chain_id = chain_id
        self._local.step_id = 0
        self._local.call_depth = 0
        try:
            self._run_chain(start)
        except Exception:
            self.log_message.emit(traceback.format_exc())

    def _run_chain(self, node):
        current = node
        while current is not None and not self._stop_event.is_set():
            self._local.step_id += 1
            self.node_started.emit(current.id)
            self._wait_if_paused(current)
            if self._stop_event.is_set():
                self.node_finished.emit(current.id)
                break
            try:
                next_port_name = current.execute(self)
            except Exception as exc:
                self.log_message.emit(f"[{current.name()}] ошибка: {exc}")
                next_port_name = None
            self.node_finished.emit(current.id)
            current = self._next_node(current, next_port_name)

    def _wait_if_paused(self, node):
        with self._pause_cond:
            if node.get_property(BREAKPOINT_PROP):
                self._paused = True
            if not self._paused:
                return
            if self._step_budget > 0:
                self._step_budget -= 1
                return
            self.paused.emit(node.id)
            while self._paused and not self._stop_event.is_set():
                if self._step_budget > 0:
                    self._step_budget -= 1
                    return
                self._pause_cond.wait(0.1)
        if not self._stop_event.is_set():
            self.resumed.emit()

    def _next_node(self, node, out_port_name):
        if not out_port_name:
            return None
        return self._exec_next.get((node.id, out_port_name))

    def cache_key(self):
        """(цепочка, (запуск, шаг)) — ключ кэша ``DataNode`` в текущем потоке."""
        return getattr(self._local, "chain_id", 0), (self.run_id, getattr(self._local, "step_id", 0))

    @property
    def step_id(self):
        return getattr(self._local, "step_id", 0)

    def get_input_value(self, node, port_name):
        source = self._data_src.get((node.id, port_name))
        if source is None:
            if port_name not in node.inputs():
                return None
            return node.get_property(port_name)

        src_node, src_port_name = source
        if isinstance(src_node, DataNode):
            return src_node.get_output_value(self, src_port_name)
        if isinstance(src_node, ExecNode):
            return src_node.get_output_value(src_port_name)
        return None

    # ---- переменные (общие для всех цепочек запуска) ----
    def get_variable(self, name, default=None):
        with self._variables_lock:
            return self._variables.get(name, default)

    def set_variable(self, name, value):
        with self._variables_lock:
            self._variables[name] = value

    def update_variable(self, name, func, default=None):
        """Атомарно ``переменная = func(переменная)`` — чтобы «Увеличить»
        из двух параллельных цепочек не терял приращения."""
        with self._variables_lock:
            value = func(self._variables.get(name, default))
            self._variables[name] = value
            return value

    # ---- функции ----
    def call_function(self, name):
        """Выполняет цепочку от ноды «Функция» с этим именем до её конца и
        возвращает управление вызвавшей ноде."""
        func_node = self._functions.get(name)
        if func_node is None:
            raise LookupError(f"функция «{name}» не найдена")
        depth = getattr(self._local, "call_depth", 0)
        if depth >= MAX_CALL_DEPTH:
            raise RecursionError(f"слишком глубокая вложенность вызовов (>{MAX_CALL_DEPTH})")
        self._local.call_depth = depth + 1
        try:
            self._run_chain(func_node)
        finally:
            self._local.call_depth = depth

    def sleep_interruptible(self, seconds):
        """Прерываемая пауза, чтобы «Остановить» срабатывало мгновенно
        даже во время ожидания в ноде Wait."""
        self._stop_event.wait(max(0.0, seconds))
