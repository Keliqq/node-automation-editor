import time

import pytest

from core.ports import BREAKPOINT_PROP
from nodes.actions import ClickNode
from nodes.events import CallFunctionNode, FunctionNode, StartNode
from nodes.flow import CompareNode, IfElseNode, WhileLoopNode
from nodes.variables import GetVariableNode, IncrementVariableNode, SetVariableNode
from tests.conftest import CounterDataNode, ReadTwiceNode, RecordNode


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_linear_chain_runs_in_order(g):
    start = g.node(StartNode)
    a = g.node(RecordNode, метка="a")
    b = g.node(RecordNode, метка="b")
    g.wire(start, "выход", a)
    g.wire(a, "выход", b)

    engine = g.engine()
    engine.run()
    assert engine.records == ["a", "b"]


def test_no_start_node_logs_and_finishes(g):
    g.node(RecordNode, метка="a")
    engine = g.engine()
    engine.run()
    assert engine.records == []
    assert any("Старт" in m for m in engine.logs)


@pytest.mark.parametrize("condition, expected", [(True, ["да"]), (False, ["нет"])])
def test_if_else_follows_branch(g, condition, expected):
    start = g.node(StartNode)
    branch = g.node(IfElseNode, условие=condition)
    yes = g.node(RecordNode, метка="да")
    no = g.node(RecordNode, метка="нет")
    g.wire(start, "выход", branch)
    g.wire(branch, "истина", yes)
    g.wire(branch, "ложь", no)

    engine = g.engine()
    engine.run()
    assert engine.records == expected


def _loop_graph(g, iterations):
    start = g.node(StartNode)
    loop = g.node(WhileLoopNode, макс_итераций=iterations)
    body = g.node(RecordNode, метка="тело")
    done = g.node(RecordNode, метка="конец")
    g.wire(start, "выход", loop)
    g.wire(loop, "тело_цикла", body)
    g.wire(body, "выход", loop)
    g.wire(loop, "завершено", done)
    return loop


def test_while_loop_counter_resets_between_runs(g):
    loop = _loop_graph(g, 3)
    for _ in range(2):
        engine = g.engine()
        engine.run()
        assert engine.records == ["тело"] * 3 + ["конец"]

    # Прерванный посреди цикла счётчик не должен перетекать в следующий запуск.
    loop._iterations = 2
    engine = g.engine()
    engine.run()
    assert engine.records == ["тело"] * 3 + ["конец"]


def test_data_node_computed_once_per_step(g):
    start = g.node(StartNode)
    counter = g.node(CounterDataNode)
    first = g.node(ReadTwiceNode)
    second = g.node(ReadTwiceNode)
    g.wire(start, "выход", first)
    g.wire(first, "выход", second)
    g.wire(counter, "n", first, "v")
    g.wire(counter, "n", second, "v")

    engine = g.engine()
    engine.run()
    assert engine.records == [(1, 1), (2, 2)]


def test_click_with_missing_coordinates_stops_branch(g, monkeypatch):
    clicks = []
    monkeypatch.setattr("pyautogui.click", lambda **kw: clicks.append(kw))
    start = g.node(StartNode)
    missing = g.node(GetVariableNode, имя="нет_такой")
    click = g.node(ClickNode, y=5)
    after = g.node(RecordNode, метка="после")
    g.wire(start, "выход", click)
    g.wire(click, "выход", after)
    g.wire(missing, "значение", click, "x")

    engine = g.engine()
    engine.run()
    assert clicks == []
    assert engine.records == []
    assert any("координаты не заданы" in m for m in engine.logs)


def test_click_at_zero_is_allowed(g, monkeypatch):
    clicks = []
    monkeypatch.setattr("pyautogui.click", lambda **kw: clicks.append((kw["x"], kw["y"])))
    start = g.node(StartNode)
    click = g.node(ClickNode, x=0, y=0)
    g.wire(start, "выход", click)

    g.engine().run()
    assert clicks == [(0, 0)]


def test_compare_with_missing_value_is_false(g):
    start = g.node(StartNode)
    missing = g.node(GetVariableNode, имя="нет_такой")
    compare = g.node(CompareNode, с_чем=100, операция="<")
    branch = g.node(IfElseNode)
    yes = g.node(RecordNode, метка="да")
    no = g.node(RecordNode, метка="нет")
    g.wire(missing, "значение", compare, "значение")
    g.wire(compare, "результат", branch, "условие")
    g.wire(start, "выход", branch)
    g.wire(branch, "истина", yes)
    g.wire(branch, "ложь", no)

    engine = g.engine()
    engine.run()
    assert engine.records == ["нет"]


def test_variables_counter_in_loop(g):
    start = g.node(StartNode)
    init = g.node(SetVariableNode, имя="n", значение="10")
    loop = g.node(WhileLoopNode, макс_итераций=4)
    inc = g.node(IncrementVariableNode, имя="n", шаг=2)
    read = g.node(GetVariableNode, имя="n")
    report = g.node(RecordNode)
    g.wire(start, "выход", init)
    g.wire(init, "выход", loop)
    g.wire(loop, "тело_цикла", inc)
    g.wire(inc, "выход", loop)
    g.wire(loop, "завершено", report)
    g.wire(read, "значение", report, "метка")

    engine = g.engine()
    engine.run()
    assert engine.records == [18]


def test_function_call_returns_to_caller(g):
    start = g.node(StartNode)
    call = g.node(CallFunctionNode, имя="f")
    after = g.node(RecordNode, метка="после")
    func = g.node(FunctionNode, имя="f")
    inside = g.node(RecordNode, метка="внутри")
    g.wire(start, "выход", call)
    g.wire(call, "выход", after)
    g.wire(func, "выход", inside)

    engine = g.engine()
    engine.run()
    assert engine.records == ["внутри", "после"]


def test_unknown_function_logs_error(g):
    start = g.node(StartNode)
    call = g.node(CallFunctionNode, имя="нет")
    g.wire(start, "выход", call)

    engine = g.engine()
    engine.run()
    assert any("не найдена" in m for m in engine.logs)


def test_wiring_is_snapshotted_at_engine_creation(g):
    start = g.node(StartNode)
    a = g.node(RecordNode, метка="a")
    g.wire(start, "выход", a)

    engine = g.engine()
    start.outputs()["выход"].disconnect_from(a.inputs()["вход"])
    engine.run()
    assert engine.records == ["a"]


def test_parallel_start_chains(g):
    """Бесконечный цикл в одной цепочке не мешает второй."""
    start1 = g.node(StartNode)
    loop = g.node(WhileLoopNode, макс_итераций=10**9)
    spin = g.node(RecordNode, метка="крутимся")
    g.wire(start1, "выход", loop)
    g.wire(loop, "тело_цикла", spin)
    g.wire(spin, "выход", loop)

    start2 = g.node(StartNode)
    other = g.node(RecordNode, метка="вторая")
    g.wire(start2, "выход", other)

    engine = g.engine()
    engine.start()
    try:
        assert wait_until(lambda: "вторая" in engine.records)
    finally:
        engine.stop()
        assert engine.wait(5000)


def test_breakpoint_pause_step_resume(g):
    start = g.node(StartNode)
    a = g.node(RecordNode, метка="a")
    b = g.node(RecordNode, метка="b")
    c = g.node(RecordNode, метка="c")
    g.wire(start, "выход", a)
    g.wire(a, "выход", b)
    g.wire(b, "выход", c)
    a.set_property(BREAKPOINT_PROP, True, push_undo=False)

    engine = g.engine()
    engine.start()
    try:
        assert wait_until(engine.is_paused)
        time.sleep(0.1)
        assert engine.records == []

        engine.step()  # выполняет «a», встаёт перед «b»
        assert wait_until(lambda: engine.records == ["a"])
        time.sleep(0.1)
        assert engine.records == ["a"]

        engine.resume()
        assert wait_until(lambda: engine.records == ["a", "b", "c"])
    finally:
        engine.stop()
        assert engine.wait(5000)


def test_stop_while_paused(g):
    start = g.node(StartNode)
    a = g.node(RecordNode, метка="a")
    g.wire(start, "выход", a)

    engine = g.engine(start_paused=True)
    engine.start()
    assert wait_until(engine.is_paused)
    engine.stop()
    assert engine.wait(5000)
    assert engine.records == []
