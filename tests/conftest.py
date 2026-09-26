"""Общие фикстуры: настоящий ``NodeGraph`` на offscreen-Qt (без окна) и
пара тестовых нод, которые записывают, что и в каком порядке выполнилось."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from NodeGraphQt import NodeGraph  # noqa: E402
from PySide6 import QtWidgets  # noqa: E402

import core.nodegraphqt_patches  # noqa: E402,F401
from core.engine import GraphEngine  # noqa: E402
from core.exec_node import DataNode, ExecNode  # noqa: E402
from nodes import BUILTIN_NODES  # noqa: E402


class RecordNode(ExecNode):
    """Дописывает «метку» (или значение с провода) в ``engine.records``."""

    __identifier__ = "tests"
    NODE_NAME = "Record"

    def __init__(self):
        super().__init__()
        self.add_exec_input()
        self.add_exec_output()
        self.add_data_input("метка", default="")

    def execute(self, engine):
        engine.records.append(engine.get_input_value(self, "метка"))
        return "выход"


class CounterDataNode(DataNode):
    """Отдаёт, сколько раз её пересчитали — чтобы проверить кэш по шагам."""

    __identifier__ = "tests"
    NODE_NAME = "Counter"

    def __init__(self):
        super().__init__()
        self.add_data_output("n")
        self.calls = 0

    def compute(self, engine):
        self.calls += 1
        return {"n": self.calls}


class ReadTwiceNode(ExecNode):
    """Читает один и тот же вход дважды за шаг."""

    __identifier__ = "tests"
    NODE_NAME = "ReadTwice"

    def __init__(self):
        super().__init__()
        self.add_exec_input()
        self.add_exec_output()
        self.add_data_input("v")

    def execute(self, engine):
        engine.records.append((engine.get_input_value(self, "v"), engine.get_input_value(self, "v")))
        return "выход"


@pytest.fixture(scope="session")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def graph(qapp):
    g = NodeGraph()
    g.set_acyclic(False)
    g.register_nodes(BUILTIN_NODES + [RecordNode, CounterDataNode, ReadTwiceNode])
    yield g
    g.clear_session()
    g.widget.deleteLater()


class Helper:
    def __init__(self, graph):
        self.graph = graph

    def node(self, cls, **props):
        node = self.graph.create_node(f"{cls.__identifier__}.{cls.__name__}", push_undo=False)
        for name, value in props.items():
            node.set_property(name, value, push_undo=False)
        return node

    def wire(self, src, out_port, dst, in_port="вход"):
        src.outputs()[out_port].connect_to(dst.inputs()[in_port])

    def engine(self, **kwargs):
        engine = GraphEngine(self.graph, **kwargs)
        engine.records = []
        engine.logs = []
        engine.log_message.connect(engine.logs.append)
        return engine


@pytest.fixture
def g(graph):
    return Helper(graph)
