"""ИИ-ассистент графа: чат с моделью -> план графа в ответе -> проверка ->
ноды и провода в графе.

Модель не видит код: ей отдаётся каталог нод, собранный из самих классов
(порты, свойства, значения по умолчанию, docstring), поэтому новые ноды из
``user_nodes/`` попадают в каталог сами. План проверяется до того, как
что-то создаётся: несуществующие ноды/порты, провод exec в data и т.п.
возвращаются модели на исправление, а не превращаются в сломанный граф.

Разговор идёт свободным текстом (модель может сначала задать вопросы), а
план — JSON в блоке ```json внутри ответа: так один и тот же протокол
работает и с Claude, и с любой OpenAI-совместимой моделью, включая
локальные без поддержки structured output / tool calling.

Пути к шаблонам модель не задаёт — только описание, что на шаблоне должно
быть; сами шаблоны потом делает ``core.ai_template_dialog`` по скриншоту.
"""

import json
import re

from NodeGraphQt import NodeGraph
from NodeGraphQt.constants import NodePropWidgetEnum

from core.ai_vision import assistant_turn, encode_screenshot, stream_chat, user_turn
from core.ports import BREAKPOINT_PROP, EXEC_PORT_COLOR

TEMPLATE_PROP = "путь_к_шаблону"
START_NODE_NAME = "Старт"
FUNCTION_NODE_NAME = "Функция"

# Свойства, которые модель не трогает: путь к шаблону делается отдельно,
# точка останова — дело отладки, а не логики.
_HIDDEN_PROPS = {TEMPLATE_PROP, BREAKPOINT_PROP}

# Тип значения свойства — по виджету, а не по значению по умолчанию: у
# «Сравнить числа» default=0 (int), а поле дробное.
_KIND_BY_WIDGET = {
    NodePropWidgetEnum.QCHECK_BOX.value: "bool",
    NodePropWidgetEnum.INT.value: "int",
    NodePropWidgetEnum.FLOAT.value: "float",
}

_SYSTEM_PROMPT = """\
Ты — помощник в визуальном редакторе автоматизации рабочего стола (клики, \
клавиатура, поиск картинок на экране, OCR). Вы с пользователем в чате: он \
описывает, что должна делать программа, а ты собираешь для этого граф из нод.

Как вести разговор:
- Сначала пойми задачу. Если неясно, КАК именно она делается в программе/игре \
(по чему кликать, что происходит после клика, когда остановиться), задай \
уточняющие вопросы — не больше трёх за раз — и не выдавай план, пока не \
разберёшься. Не угадывай механику игры: лучше спроси. Если приложен скриншот, \
скажи, что на нём видишь, и опирайся на это в вопросах.
- Когда всё ясно — коротко опиши, что будет делать граф, и выдай план в одном \
блоке ```json ... ``` (формат ниже). В каждом ответе — не больше одного плана.
- Если пользователь просит что-то поменять, выдай новый план целиком.
- Если программа сообщит об ошибках в плане, исправь их и выдай план целиком.
- Пиши по-русски, коротко.

Как работает граф:
- Выполнение начинается с ноды «Старт» и идёт по exec-проводам: каждая \
exec-нода делает действие и продолжает ровно по одному своему exec-выходу.
- Цепочка заканчивается там, где exec-выход никуда не подключён. Поэтому \
каждый exec-выход (в том числе «таймаут», «ложь», «завершено») должен вести \
туда, где работа продолжается, — если пользователь не просил остановиться.
- Повтор/цикл — это exec-провод обратно на вход более ранней ноды. \
«Повторять бесконечно» = провод с последней ноды цепочки на первую ноду после «Старт».
- В каждом цикле должна быть пауза («Ждать» или «Ждать картинку»), иначе \
цикл крутится без остановки и грузит процессор.
- Data-ноды (без exec-портов) считаются, когда кто-то читает их выход.
- Data-вход можно не подключать: тогда берётся значение из properties ноды.
- exec-выход соединяется только с exec-входом, data-выход — только с data-входом. \
На один exec-выход — один провод; на один data-вход — один провод.
- Чтобы кликнуть по найденной картинке, соедини выходы x и y ноды поиска \
картинки со входами x и y «Клик». Для ожидания появления чего-то на экране \
удобнее «Ждать картинку» (выходы «найдено»/«таймаут»).
- После клика, который открывает окно или меню, дождись этого окна («Ждать \
картинку») прежде, чем кликать по нему.

Шаблоны картинок:
- У нод с путём к шаблону НЕ задавай путь в properties — заполни поле \
template коротким описанием того, что должно быть на шаблоне (по нему шаблон \
вырежут со скриншота). Описывай неизменную часть объекта: если внутри \
объекта содержимое меняется (облако с разными предметами), опиши рамку/хвостик, \
а не содержимое. У остальных нод template — пустая строка.
- Объект, которого сейчас нет на экране (например, окно, появляющееся после \
клика), всё равно опиши в template и упомяни в notes, что шаблон для него \
сделают, когда он появится.
- Не задавай координаты x/y и «область» по скриншоту наугад.

Формат плана (строго JSON в блоке ```json):
{
  "summary": "одно-два предложения, что делает граф",
  "nodes": [
    {"id": "n1", "type": "<название ноды из каталога>",
     "properties": [{"name": "<свойство>", "value": "<значение строкой>"}],
     "template": "<описание шаблона или пустая строка>"}
  ],
  "connections": [
    {"from_node": "n1", "from_port": "<выход>", "to_node": "n2", "to_port": "<вход>"}
  ],
  "notes": "что пользователю проверить или доделать руками"
}
- Используй только ноды из каталога, названия нод, портов и свойств пиши \
точно как в каталоге. Не выдумывай нод. Если чего-то не хватает — собери, \
что можно, и скажи об этом.
- id нод — короткие латинские (n1, n2, ...). Значения properties — строки \
(числа и true/false тоже строкой). Для свойств со списком вариантов — один из вариантов.
- В properties указывай только то, что отличается от значения по умолчанию.

Каталог нод:
"""


def build_catalog(node_classes):
    """Описание нод для модели и для проверки плана.

    Порты и свойства читаются с настоящих экземпляров во временном графе —
    так каталог всегда совпадает с кодом нод, включая пользовательские.
    Вызывать из GUI-потока (создаёт Qt-объекты).

    Returns:
        dict[str, dict]: NODE_NAME -> описание (см. ключи ниже).
    """
    graph = NodeGraph()
    try:
        graph.register_nodes(node_classes)
        catalog = {}
        for cls in node_classes:
            node_type = f"{cls.__identifier__}.{cls.__name__}"
            if cls.NODE_NAME in catalog:
                continue  # одноимённая нода — модель всё равно различит только по имени
            node = graph.create_node(node_type, push_undo=False)
            common = graph.model.get_node_common_properties(node_type) or {}
            entry = {
                "type": node_type,
                "doc": _first_paragraph(cls.__doc__),
                "exec_in": [],
                "exec_out": [],
                "data_in": [],
                "data_out": [],
                "props": {},
            }
            for name, port in node.inputs().items():
                entry["exec_in" if tuple(port.color) == EXEC_PORT_COLOR else "data_in"].append(name)
            for name, port in node.outputs().items():
                entry["exec_out" if tuple(port.color) == EXEC_PORT_COLOR else "data_out"].append(name)
            for name, default in node.model.custom_properties.items():
                entry["props"][name] = {
                    "default": default,
                    "kind": _KIND_BY_WIDGET.get(node.model.get_widget_type(name), "str"),
                    "items": list((common.get(name) or {}).get("items") or []) or None,
                }
            catalog[cls.NODE_NAME] = entry
        return catalog
    finally:
        graph.clear_session()
        graph.widget.deleteLater()


def _first_paragraph(doc):
    paragraph = (doc or "").strip().split("\n\n")[0]
    return " ".join(line.strip() for line in paragraph.splitlines())


_KIND_LABELS = {"bool": "true/false", "int": "целое", "float": "число", "str": "строка"}


def catalog_text(catalog):
    lines = []
    for name, entry in catalog.items():
        kind = "exec-нода" if entry["exec_in"] or entry["exec_out"] else "data-нода"
        lines.append(f"\n«{name}» ({kind}) — {entry['doc'] or 'без описания'}")
        if entry["exec_in"]:
            lines.append(f"  exec-входы: {', '.join(entry['exec_in'])}")
        if entry["exec_out"]:
            lines.append(f"  exec-выходы: {', '.join(entry['exec_out'])}")
        if entry["data_out"]:
            lines.append(f"  data-выходы: {', '.join(entry['data_out'])}")
        props = []
        for prop, info in entry["props"].items():
            if prop in _HIDDEN_PROPS:
                continue
            where = "data-вход" if prop in entry["data_in"] else "свойство"
            if info["items"]:
                props.append(f"{prop} ({where}, одно из: {' | '.join(map(str, info['items']))}; по умолчанию {info['default']})")
            else:
                props.append(f"{prop} ({where}, {_KIND_LABELS[info['kind']]}, по умолчанию {info['default']!r})")
        if props:
            lines.append(f"  входы/свойства: {'; '.join(props)}")
        if TEMPLATE_PROP in entry["props"]:
            lines.append("  шаблон картинки: заполни template")
    return "\n".join(lines)


def convert_value(text, kind):
    """Строковое значение из плана -> значение свойства вида ``kind``."""
    text = str(text).strip()
    if kind == "bool":
        lowered = text.lower()
        if lowered in ("true", "1", "да", "истина", "yes"):
            return True
        if lowered in ("false", "0", "нет", "ложь", "no", ""):
            return False
        raise ValueError(f"ожидалось true/false, а не {text!r}")
    if kind == "int":
        return int(float(text.replace(",", ".")))
    if kind == "float":
        return float(text.replace(",", "."))
    return text


def validate_plan(plan, catalog):
    """Список ошибок плана (пустой — план можно строить)."""
    # План приходит свободным текстом — сначала форма, потом смысл.
    for key in ("nodes", "connections"):
        items = plan.get(key)
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            return [f"«{key}» должен быть списком объектов"]
    for node in plan["nodes"]:
        props = node.get("properties") or []
        if not isinstance(props, list) or not all(isinstance(p, dict) for p in props):
            return [f"{node.get('id')}: «properties» должен быть списком объектов {{name, value}}"]

    errors = []
    nodes = {}
    for node in plan["nodes"]:
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            errors.append("у ноды нет id")
            continue
        if node_id in nodes:
            errors.append(f"id «{node_id}» повторяется")
            continue
        entry = catalog.get(node.get("type"))
        if entry is None:
            errors.append(f"{node_id}: нет ноды «{node.get('type')}» в каталоге")
            continue
        nodes[node_id] = entry
        for prop in node.get("properties") or []:
            name = prop.get("name")
            info = entry["props"].get(name)
            if info is None or name in _HIDDEN_PROPS:
                errors.append(f"{node_id}: у «{node.get('type')}» нет свойства «{name}»")
                continue
            if info["items"]:
                if str(prop.get("value")) not in map(str, info["items"]):
                    errors.append(f"{node_id}.{name}: «{prop.get('value')}» не из вариантов {info['items']}")
                continue
            try:
                convert_value(prop.get("value"), info["kind"])
            except ValueError as exc:
                errors.append(f"{node_id}.{name}: {exc}")
        if (node.get("template") or "").strip() and TEMPLATE_PROP not in entry["props"]:
            errors.append(f"{node_id}: у «{node.get('type')}» нет шаблона картинки, template должен быть пустым")

    if not any(node.get("type") == START_NODE_NAME for node in plan.get("nodes") or []):
        errors.append(f"в графе нет ноды «{START_NODE_NAME}»")

    used_exec_out, used_data_in, has_exec_input = set(), set(), set()
    for conn in plan.get("connections") or []:
        src, src_port = conn.get("from_node"), conn.get("from_port")
        dst, dst_port = conn.get("to_node"), conn.get("to_port")
        label = f"{src}.{src_port} -> {dst}.{dst_port}"
        if src not in nodes or dst not in nodes:
            errors.append(f"{label}: нет ноды с таким id")
            continue
        src_entry, dst_entry = nodes[src], nodes[dst]
        if src_port in src_entry["exec_out"]:
            src_kind = "exec"
        elif src_port in src_entry["data_out"]:
            src_kind = "data"
        else:
            errors.append(f"{label}: у {src} нет выхода «{src_port}»")
            continue
        if dst_port in dst_entry["exec_in"]:
            dst_kind = "exec"
        elif dst_port in dst_entry["data_in"]:
            dst_kind = "data"
        else:
            errors.append(f"{label}: у {dst} нет входа «{dst_port}»")
            continue
        if src_kind != dst_kind:
            errors.append(f"{label}: нельзя соединять {src_kind}-выход с {dst_kind}-входом")
            continue
        if src_kind == "exec":
            if (src, src_port) in used_exec_out:
                errors.append(f"{label}: из exec-выхода {src}.{src_port} уже идёт провод (можно только один)")
            used_exec_out.add((src, src_port))
            has_exec_input.add(dst)
        else:
            if (dst, dst_port) in used_data_in:
                errors.append(f"{label}: в data-вход {dst}.{dst_port} уже идёт провод (можно только один)")
            used_data_in.add((dst, dst_port))

    entry_points = (catalog.get(START_NODE_NAME), catalog.get(FUNCTION_NODE_NAME))
    for node_id, entry in nodes.items():
        if entry["exec_in"] and node_id not in has_exec_input and entry not in entry_points:
            errors.append(f"{node_id}: на exec-вход ничего не подключено — нода никогда не выполнится")
    return errors


_PLAN_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_plan(text):
    """Находит план графа в ответе модели.

    Returns:
        tuple[dict | None, str]: последний план (объект с "nodes") или None и
            текст ответа без блока плана — его чату незачем показывать.
    """
    plan = None
    span = None
    for match in _PLAN_BLOCK.finditer(text or ""):
        try:
            value = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "nodes" in value:
            plan, span = value, match.span()
    if plan is None:
        return None, text or ""
    plan.setdefault("summary", "")
    plan.setdefault("notes", "")
    plan.setdefault("connections", [])
    # id приводим к строкам: модель может написать 1 вместо "n1" — лишь бы
    # одинаково в nodes и connections.
    for spec in plan.get("nodes") or []:
        if isinstance(spec, dict):
            spec["id"] = str(spec.get("id") or "").strip()
            spec.setdefault("properties", [])
            spec["template"] = str(spec.get("template") or "")
    for conn in plan.get("connections") or []:
        if isinstance(conn, dict):
            for key in ("from_node", "to_node"):
                conn[key] = str(conn.get(key) or "").strip()
    return plan, (text[: span[0]] + text[span[1] :]).strip()


class GraphChat:
    """Разговор с моделью о графе: история ходов, скриншоты, проверка
    выданного плана. Без Qt — окно чата только показывает то, что здесь
    происходит, а тесты гоняют этот класс с поддельным клиентом."""

    def __init__(self, catalog, settings=None, client=None):
        self.catalog = catalog
        self._system = _SYSTEM_PROMPT + catalog_text(catalog)
        self._settings = settings
        self._client = client
        self.turns = []

    def send(self, text, screenshot=None, on_thinking=lambda _: None, on_text=lambda _: None, should_stop=lambda: False):
        """Отправляет сообщение и ждёт ответ целиком (куски — в колбэки).

        Блокирующий сетевой вызов — не вызывать из GUI-потока.

        Returns:
            dict: {"text": ответ без блока плана, "plan": план | None,
                "errors": ошибки плана, "stopped": оборван ли «Стопом»}.
        """
        png = None
        if screenshot is not None:
            png, _, _, _ = encode_screenshot(screenshot, self._settings)
            text += "\n\n(Приложен скриншот экрана.)"
        self.turns.append(user_turn(text, png))
        try:
            reply = stream_chat(self._system, self.turns, on_thinking, on_text, should_stop, self._settings, self._client)
        except Exception:
            self.turns.pop()  # чтобы «повторить» не отправило вопрос дважды
            raise
        stopped = should_stop()
        if stopped:
            reply = (reply + "\n\n[ответ прерван пользователем]").strip()
        self.turns.append(assistant_turn(reply))
        plan, visible = extract_plan(reply)
        errors = validate_plan(plan, self.catalog) if plan is not None and not stopped else []
        return {"text": visible, "plan": None if stopped else plan, "errors": errors, "stopped": stopped}

    @staticmethod
    def fix_request(errors):
        return "Программа нашла ошибки в плане:\n- " + "\n- ".join(errors) + "\nИсправь их и выдай план целиком."


def apply_plan(graph, plan, catalog, template_paths=None):
    """Создаёт ноды и провода плана в ``graph`` одним шагом отмены.

    План должен быть уже проверен ``validate_plan``.

    Args:
        template_paths: id ноды -> путь к PNG-шаблону.

    Returns:
        dict[str, BaseNode]: id из плана -> созданная нода.
    """
    template_paths = template_paths or {}
    positions = layout_plan(plan, catalog)
    created = {}
    graph.begin_undo("ИИ: собрать граф")
    try:
        for spec in plan["nodes"]:
            entry = catalog[spec["type"]]
            # Невыделенными: выделение/снятие выделения в NodeGraphQt — тоже
            # шаги отмены, а граф должен отменяться одним «Отменить».
            node = graph.create_node(entry["type"], selected=False, pos=list(positions[spec["id"]]))
            for prop in spec.get("properties") or []:
                info = entry["props"][prop["name"]]
                value = str(prop["value"]) if info["items"] else convert_value(prop["value"], info["kind"])
                node.set_property(prop["name"], value)
            if spec["id"] in template_paths:
                node.set_property(TEMPLATE_PROP, template_paths[spec["id"]])
            created[spec["id"]] = node
        for conn in plan["connections"]:
            src = created[conn["from_node"]].outputs()[conn["from_port"]]
            src.connect_to(created[conn["to_node"]].inputs()[conn["to_port"]])
    finally:
        graph.end_undo()
    return created



COLUMN_WIDTH = 320
ROW_HEIGHT = 200


def layout_plan(plan, catalog):
    """Позиции нод: колонка = шаг от «Старт»/«Функция» по exec-проводам,
    data-ноды — под той нодой, что читает их выход.

    Своя раскладка, а не ``NodeGraph.auto_layout_nodes``: та уходит в
    бесконечную рекурсию на циклах, а здесь почти любой граф — цикл.

    Returns:
        dict[str, tuple[int, int]]: id ноды -> (x, y).
    """
    specs = {spec["id"]: spec for spec in plan["nodes"]}
    exec_next, data_consumer = {}, {}
    for conn in plan["connections"]:
        entry = catalog[specs[conn["from_node"]]["type"]]
        if conn["from_port"] in entry["exec_out"]:
            exec_next.setdefault(conn["from_node"], []).append(conn["to_node"])
        else:
            data_consumer.setdefault(conn["from_node"], conn["to_node"])

    roots = [i for i, spec in specs.items() if spec["type"] in (START_NODE_NAME, FUNCTION_NODE_NAME)]
    column = {}
    queue = list(roots)
    for root in roots:
        column[root] = 0
    while queue:  # обход в ширину: обратные провода циклов не двигают ноды дальше
        current = queue.pop(0)
        for nxt in exec_next.get(current, []):
            if nxt not in column:
                column[nxt] = column[current] + 1
                queue.append(nxt)
    # Exec-ноды, до которых не дошли (план с ошибкой) — в колонку справа.
    tail = max(column.values(), default=-1) + 1
    for node_id, spec in specs.items():
        entry = catalog[spec["type"]]
        if node_id not in column and (entry["exec_in"] or entry["exec_out"]):
            column[node_id] = tail

    positions, rows = {}, {}
    for node_id in specs:
        if node_id in column:
            row = rows.get(column[node_id], 0)
            rows[column[node_id]] = row + 1
            positions[node_id] = (column[node_id] * COLUMN_WIDTH, row * ROW_HEIGHT * 2)

    below = {}
    pending = [i for i in specs if i not in positions]
    for _ in range(len(pending) + 1):  # data -> data -> exec: ставим по мере готовности читателя
        for node_id in list(pending):
            consumer = data_consumer.get(node_id)
            if consumer is not None and consumer not in positions:
                continue
            if consumer is None:
                base_x, base_y = tail * COLUMN_WIDTH, 0
            else:
                base_x, base_y = positions[consumer]
            count = below.get(consumer, 0) + 1
            below[consumer] = count
            positions[node_id] = (base_x - COLUMN_WIDTH // 3, base_y + count * ROW_HEIGHT)
            pending.remove(node_id)
    for node_id in pending:  # цепочка data-нод по кругу — просто в угол
        positions[node_id] = (tail * COLUMN_WIDTH, -ROW_HEIGHT)
    return positions
