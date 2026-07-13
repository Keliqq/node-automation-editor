import threading
import time
import traceback

from PySide6 import QtCore

from core.exec_node import DataNode, ExecNode


class GraphEngine(QtCore.QThread):
    """Проходит по exec-графу в фоновом потоке.

    Работает вне GUI-потока, чтобы долгий цикл автоматизации не замораживал
    редактор, и чтобы «Остановить» срабатывало сразу между шагами, а не
    только после завершения всего графа.
    """

    node_started = QtCore.Signal(str)
    node_finished = QtCore.Signal(str)
    log_message = QtCore.Signal(str)
    run_finished = QtCore.Signal()

    def __init__(self, graph, parent=None):
        super().__init__(parent)
        self.graph = graph
        self.step_id = 0
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def is_stopping(self):
        return self._stop_event.is_set()

    def run(self):
        from nodes.events import StartNode

        self._stop_event.clear()
        self.step_id = 0

        start_nodes = [n for n in self.graph.all_nodes() if isinstance(n, StartNode)]
        if not start_nodes:
            self.log_message.emit(
                "Нет узла «Старт» на графе — добавьте его, чтобы запустить выполнение."
            )
            self.run_finished.emit()
            return

        try:
            for start in start_nodes:
                if self._stop_event.is_set():
                    break
                self._run_chain(start)
        except Exception:
            self.log_message.emit(traceback.format_exc())

        self.run_finished.emit()

    def _run_chain(self, node):
        current = node
        while current is not None and not self._stop_event.is_set():
            self.step_id += 1
            self.node_started.emit(current.id)
            try:
                next_port_name = current.execute(self)
            except Exception as exc:
                self.log_message.emit(f"[{current.name()}] ошибка: {exc}")
                next_port_name = None
            self.node_finished.emit(current.id)
            current = self._next_node(current, next_port_name)

    def _next_node(self, node, out_port_name):
        if not out_port_name:
            return None
        out_port = node.outputs().get(out_port_name)
        if out_port is None:
            return None
        connected = out_port.connected_ports()
        if not connected:
            return None
        return connected[0].node()

    def get_input_value(self, node, port_name):
        in_port = node.inputs().get(port_name)
        if in_port is None:
            return None
        connected = in_port.connected_ports()
        if not connected:
            return node.get_property(port_name)

        src_port = connected[0]
        src_node = src_port.node()
        if isinstance(src_node, DataNode):
            return src_node.get_output_value(self, src_port.name())
        if isinstance(src_node, ExecNode):
            return src_node.get_output_value(src_port.name())
        return None

    def sleep_interruptible(self, seconds):
        """Прерываемая пауза, чтобы «Остановить» срабатывало мгновенно
        даже во время ожидания в ноде Wait."""
        end_time = time.time() + max(0.0, seconds)
        while time.time() < end_time and not self._stop_event.is_set():
            time.sleep(min(0.05, end_time - time.time()))
