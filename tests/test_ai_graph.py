import json
from types import SimpleNamespace

import pytest

from core.ai_graph import GraphChat, apply_plan, build_catalog, convert_value, extract_plan, layout_plan, validate_plan
from core.ai_vision import PROVIDER_ANTHROPIC
from nodes import BUILTIN_NODES
from tests.conftest import RecordNode

SETTINGS = {"provider": PROVIDER_ANTHROPIC}


@pytest.fixture(scope="module")
def catalog(qapp):
    return build_catalog(BUILTIN_NODES + [RecordNode])


def _event(kind, piece):
    field = "thinking" if kind == "thinking" else "text"
    return SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type=f"{field}_delta", **{field: piece}))


class _FakeStream:
    def __init__(self, events):
        self._events = events

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._events)

    def get_final_message(self):
        return SimpleNamespace(stop_reason="end_turn")


class ScriptedAnthropic:
    """Отдаёт заранее заданные ответы по очереди как поток событий и
    запоминает запросы — чтобы проверить историю диалога."""

    def __init__(self, *replies):
        self._replies = list(replies)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        self.requests.append(kwargs)
        reply = self._replies.pop(0)
        return _FakeStream([_event("thinking", "хм, "), _event("thinking", "думаю"), _event("text", reply)])


def reply_with(plan_obj, text="Вот граф."):
    return f"{text}\n```json\n{json.dumps(plan_obj, ensure_ascii=False)}\n```"


def node(node_id, node_type, template="", **props):
    return {
        "id": node_id,
        "type": node_type,
        "properties": [{"name": k, "value": str(v)} for k, v in props.items()],
        "template": template,
    }


def conn(src, src_port, dst, dst_port="вход"):
    return {"from_node": src, "from_port": src_port, "to_node": dst, "to_port": dst_port}


def plan(nodes, connections):
    return {"summary": "", "nodes": nodes, "connections": connections, "notes": ""}


CLICK_LOOP = plan(
    [
        node("n1", "Старт"),
        node("n2", "Ждать картинку", template="хвостик облака", таймаут_сек=5),
        node("n3", "Клик"),
        node("n4", "Ждать", секунды=1),
    ],
    [
        conn("n1", "выход", "n2"),
        conn("n2", "найдено", "n3"),
        conn("n2", "x", "n3", "x"),
        conn("n2", "y", "n3", "y"),
        conn("n2", "таймаут", "n2"),
        conn("n3", "выход", "n4"),
        conn("n4", "выход", "n2"),
    ],
)


def test_catalog_splits_exec_and_data_ports(catalog):
    wait_image = catalog["Ждать картинку"]
    assert wait_image["exec_in"] == ["вход"]
    assert wait_image["exec_out"] == ["найдено", "таймаут"]
    assert {"x", "y", "оценка"} <= set(wait_image["data_out"])
    assert catalog["Сравнить числа"]["props"]["значение"]["kind"] == "float"
    assert catalog["Клик"]["props"]["кнопка"]["items"] == ["Левая", "Правая", "Средняя"]


def test_valid_plan_has_no_errors(catalog):
    assert validate_plan(CLICK_LOOP, catalog) == []


@pytest.mark.parametrize(
    "bad_plan, fragment",
    [
        (plan([node("n1", "Старт"), node("n2", "Прыгнуть")], []), "нет ноды «Прыгнуть»"),
        (plan([node("n1", "Старт"), node("n2", "Клик", кнопка="Боковая")], [conn("n1", "выход", "n2")]), "не из вариантов"),
        (plan([node("n1", "Старт"), node("n2", "Ждать", секунды="долго")], [conn("n1", "выход", "n2")]), "n2.секунды"),
        (plan([node("n1", "Старт"), node("n2", "Клик")], [conn("n1", "выход", "n2", "x")]), "нельзя соединять exec"),
        (plan([node("n1", "Старт"), node("n2", "Клик")], [conn("n1", "финиш", "n2")]), "нет выхода «финиш»"),
        (plan([node("n1", "Старт"), node("n2", "Клик")], []), "никогда не выполнится"),
        (plan([node("n1", "Клик")], []), "нет ноды «Старт»"),
        (plan([node("n1", "Старт"), node("n2", "Клик", template="кнопка")], [conn("n1", "выход", "n2")]), "template должен быть пустым"),
        (
            plan([node("n1", "Старт"), node("n2", "Клик"), node("n3", "Клик")], [conn("n1", "выход", "n2"), conn("n1", "выход", "n3")]),
            "можно только один",
        ),
    ],
)
def test_validate_plan_catches_mistakes(catalog, bad_plan, fragment):
    errors = validate_plan(bad_plan, catalog)
    assert any(fragment in e for e in errors), errors


def test_convert_value():
    assert convert_value("да", "bool") is True
    assert convert_value("2,5", "float") == 2.5
    assert convert_value("3.0", "int") == 3
    with pytest.raises(ValueError):
        convert_value("может быть", "bool")


def test_chat_question_without_plan(catalog):
    chat = GraphChat(catalog, settings=SETTINGS, client=ScriptedAnthropic("По чему кликать — по облаку или по кнопке?"))
    thinking, text = [], []

    result = chat.send("продавай предметы", on_thinking=thinking.append, on_text=text.append)

    assert result == {"text": "По чему кликать — по облаку или по кнопке?", "plan": None, "errors": [], "stopped": False}
    assert "".join(thinking) == "хм, думаю"
    assert "".join(text) == result["text"]


def test_chat_extracts_and_validates_plan_and_keeps_history(catalog):
    broken = plan([node("n1", "Старт"), node("n2", "Прыгнуть")], [conn("n1", "выход", "n2")])
    client = ScriptedAnthropic(reply_with(broken), reply_with(CLICK_LOOP, "Исправил."))
    chat = GraphChat(catalog, settings=SETTINGS, client=client)

    first = chat.send("кликай по облакам")
    assert first["text"] == "Вот граф."
    assert any("Прыгнуть" in e for e in first["errors"])

    second = chat.send(GraphChat.fix_request(first["errors"]))
    assert second["errors"] == []
    assert second["plan"]["nodes"] == CLICK_LOOP["nodes"]
    history = client.requests[1]["messages"]
    assert [m["role"] for m in history] == ["user", "assistant", "user"]
    assert "Прыгнуть" in history[2]["content"][-1]["text"]
    assert "«Ждать картинку»" in client.requests[0]["system"]
    assert client.requests[0]["thinking"]["display"] == "summarized"


def test_chat_stop_marks_reply_and_drops_plan(catalog):
    chat = GraphChat(catalog, settings=SETTINGS, client=ScriptedAnthropic(reply_with(CLICK_LOOP)))
    result = chat.send("x", should_stop=lambda: True)
    assert result["stopped"] and result["plan"] is None
    assert "прерван" in chat.turns[-1]["text"]


def test_chat_error_does_not_leave_dangling_user_turn(catalog):
    class Failing:
        beta = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: (_ for _ in ()).throw(RuntimeError("сеть"))))

    chat = GraphChat(catalog, settings=SETTINGS, client=Failing())
    with pytest.raises(RuntimeError):
        chat.send("x")
    assert chat.turns == []


def test_extract_plan_tolerates_sloppy_json():
    text = (
        "Готово:\n```\n"
        '{"nodes": [{"id": 1, "type": "Старт"}], '
        '"connections": [{"from_node": 1, "from_port": "выход", "to_node": 2, "to_port": "вход"}]}'
        "\n```\nПроверь."
    )
    found, visible = extract_plan(text)
    assert found["nodes"][0] == {"id": "1", "type": "Старт", "properties": [], "template": ""}
    assert found["connections"][0]["to_node"] == "2"
    assert visible == "Готово:\n\nПроверь."
    assert extract_plan("просто вопрос?") == (None, "просто вопрос?")


def test_validate_plan_rejects_malformed_shapes(catalog):
    assert validate_plan({"nodes": "n1", "connections": []}, catalog) == ["«nodes» должен быть списком объектов"]
    assert validate_plan({"nodes": [{"id": "n1", "type": "Старт", "properties": {"a": 1}}], "connections": []}, catalog)


def test_apply_plan_builds_graph_the_engine_can_run(g, catalog):
    looped = plan(
        [
            node("s", "Старт"),
            node("a", "Record", метка="раз"),
            node("w", "Цикл Пока", макс_итераций=2),
            node("b", "Record", метка="тело"),
        ],
        [conn("s", "выход", "a"), conn("a", "выход", "w"), conn("w", "тело_цикла", "b"), conn("b", "выход", "w")],
    )
    assert validate_plan(looped, catalog) == []

    created = apply_plan(g.graph, looped, catalog)

    assert set(created) == {"s", "a", "w", "b"}
    assert created["w"].get_property("макс_итераций") == 2
    engine = g.engine()
    engine.run()
    assert engine.records == ["раз", "тело", "тело"]


def test_apply_plan_sets_template_paths_and_wires_data(g, catalog):
    created = apply_plan(g.graph, CLICK_LOOP, catalog, template_paths={"n2": "C:/t/cloud.png"})

    assert created["n2"].get_property("путь_к_шаблону") == "C:/t/cloud.png"
    assert created["n2"].get_property("таймаут_сек") == 5.0
    x_in = created["n3"].inputs()["x"].connected_ports()
    assert [(p.node(), p.name()) for p in x_in] == [(created["n2"], "x")]
    assert g.graph.undo_stack().count() == 1


def test_layout_puts_data_nodes_under_their_reader_without_overlap(catalog):
    with_data = plan(
        [
            node("s", "Старт"),
            node("find", "Найти картинку на экране", template="кнопка"),
            node("if", "Если / Иначе"),
            node("click", "Клик"),
            node("wait", "Ждать"),
        ],
        [
            conn("s", "выход", "if"),
            conn("find", "найдено", "if", "условие"),
            conn("if", "истина", "click"),
            conn("find", "x", "click", "x"),
            conn("if", "ложь", "wait"),
            conn("click", "выход", "wait"),
            conn("wait", "выход", "if"),
        ],
    )
    assert validate_plan(with_data, catalog) == []

    positions = layout_plan(with_data, catalog)

    assert len(set(positions.values())) == len(positions)
    assert positions["find"][1] > positions["if"][1]  # под «Если», который читает его первым
    assert positions["s"][0] < positions["if"][0] < positions["click"][0]
