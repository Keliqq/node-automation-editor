"""Поиск объекта на скриншоте по текстовому описанию через vision-модель.

Используется только при настройке графа — чтобы по описанию вроде «облако с
предметом над героем» вырезать шаблон для «Найти/Ждать картинку». Во время
работы графа ИИ не вызывается: там по-прежнему быстрый и бесплатный OpenCV.

Два провайдера (выбираются в «Настройки» → «Настройки ИИ»):
- Claude через SDK ``anthropic``;
- любой OpenAI-совместимый сервер через SDK ``openai``: OpenAI, OpenRouter,
  локальные LM Studio / Ollama / vLLM с vision-моделью (Qwen-VL и т.п.).

Модели по-разному отдают координаты: Claude — в пикселях присланной картинки
(в пределах своих лимитов), Qwen3-VL / Gemini — в долях 0..1000, и никакая
просьба в тексте запроса этого не меняет — так модель обучена. Поэтому для
OpenAI-совместимых формат можно задать в настройках, а по умолчанию («авто»)
он определяется по первому неоднозначному ответу: оба толкования рамки
вырезаются и показываются модели — «где тут объект, A или B?» — и ответ
запоминается для этой модели.
"""

import base64
import json
import re
from contextlib import contextmanager

import cv2
import numpy as np
from PySide6.QtCore import QSettings

PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENAI = "openai"

ANTHROPIC_MODEL = "claude-opus-5"

# Claude принимает до 2576 px по длинной стороне (и ~3.7 Мп по площади); в
# этих пределах её координаты совпадают с пикселями картинки 1:1. Больший
# скриншот (4K, несколько мониторов) уменьшаем сами и пересчитываем рамку.
MAX_LONG_EDGE = 2576
MAX_PIXELS = 3_700_000

_SETTINGS_ORG = "NodeAutomationEditor"
_SETTINGS_APP = "NodeEditor"
_SETTINGS_PREFIX = "ai/"

DEFAULT_SETTINGS = {
    "provider": PROVIDER_ANTHROPIC,
    "anthropic_api_key": "",
    "openai_base_url": "",  # пусто = api.openai.com
    "openai_api_key": "",
    "openai_model": "",
    # Многие серверы (и сам OpenAI) всё равно ужимают картинку; лучше
    # ужать самим и назвать модели точный размер, чем гадать о чужом.
    "openai_max_side": 1536,
    "openai_coords": "auto",  # COORDS_*
    # «модель|формат», определённый в режиме auto — чтобы не переспрашивать.
    "openai_coords_learned": "",
}

COORDS_AUTO = "auto"
COORDS_PIXELS = "pixels"
COORDS_NORMALIZED = "normalized"  # доли 0..1000 (Qwen3-VL, Gemini)

_SYSTEM_PROMPT = """\
Ты помогаешь настроить бота для автоматизации: по скриншоту и описанию нужно \
найти объект, из которого потом вырежут картинку-шаблон для OpenCV \
matchTemplate. Шаблон ищется попиксельно, поэтому:
- рамка должна плотно облегать объект, почти без фона вокруг;
- если объектов несколько, выбери один, видимый целиком и ничем не перекрытый;
- если содержимое объекта меняется от раза к разу (например, облако с разными \
предметами внутри), выдели только неизменную часть (рамку, хвостик, угол) и \
скажи об этом в note;
- если такого объекта на скриншоте нет, верни found=false.
Ответ — только JSON-объект с полями found (bool), x, y, width, height (целые) \
и note (одно короткое предложение по-русски); x, y — левый верхний угол рамки."""

_BBOX_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "x": {"type": "integer"},
        "y": {"type": "integer"},
        "width": {"type": "integer"},
        "height": {"type": "integer"},
        "note": {"type": "string"},
    },
    "required": ["found", "x", "y", "width", "height", "note"],
    "additionalProperties": False,
}


class AiVisionError(RuntimeError):
    """Понятная пользователю ошибка: нет ключа, сеть, отказ модели и т.п."""


def _settings():
    return QSettings(_SETTINGS_ORG, _SETTINGS_APP)


def get_ai_settings():
    store = _settings()
    result = {}
    for key, default in DEFAULT_SETTINGS.items():
        value = store.value(_SETTINGS_PREFIX + key, default)
        # QSettings на Windows возвращает из реестра строки — приводим назад.
        if isinstance(default, bool):
            value = value if isinstance(value, bool) else str(value).lower() == "true"
        elif isinstance(default, int):
            try:
                value = int(value)
            except (TypeError, ValueError):
                value = default
        else:
            value = str(value or "")
        result[key] = value
    # Раньше вместо openai_coords была галочка openai_normalized.
    if not store.contains(_SETTINGS_PREFIX + "openai_coords"):
        if str(store.value(_SETTINGS_PREFIX + "openai_normalized", "")).lower() == "true":
            result["openai_coords"] = COORDS_NORMALIZED
    return result


def set_ai_settings(values):
    store = _settings()
    for key in DEFAULT_SETTINGS:
        if key in values:
            value = values[key]
            store.setValue(_SETTINGS_PREFIX + key, value.strip() if isinstance(value, str) else value)


def prepare_image(bgr, max_long_edge=MAX_LONG_EDGE, max_pixels=MAX_PIXELS):
    """Уменьшает скриншот до лимитов модели и кодирует в PNG.

    Returns:
        tuple[bytes, float, int, int]: PNG, масштаб (координаты картинки /
            scale = координаты скриншота) и размер отправленной картинки.
    """
    h, w = bgr.shape[:2]
    scale = min(1.0, max_long_edge / max(w, h), (max_pixels / (w * h)) ** 0.5)
    if scale < 1.0:
        # Вниз, а не round(): округление вверх может вылезти за лимит площади.
        bgr = cv2.resize(bgr, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    ok, png = cv2.imencode(".png", bgr)
    if not ok:
        raise AiVisionError("не удалось закодировать скриншот в PNG")
    sent_h, sent_w = bgr.shape[:2]
    return png.tobytes(), scale, sent_w, sent_h


def to_image_bbox(result, scale, image_width, image_height):
    """Рамку из ответа модели -> (x, y, w, h) в пикселях исходного скриншота,
    обрезанную по его границам. None, если рамка пустая."""
    x0 = int(round(result["x"] / scale))
    y0 = int(round(result["y"] / scale))
    x1 = int(round((result["x"] + result["width"]) / scale))
    y1 = int(round((result["y"] + result["height"]) / scale))
    x0, x1 = max(0, x0), min(image_width, x1)
    y0, y1 = max(0, y0), min(image_height, y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return x0, y0, x1 - x0, y1 - y0


def parse_json_object(text, required_key):
    """Достаёт JSON-ответ из текста модели. Локальные модели без поддержки
    structured output любят обернуть его в ```json или предварить
    рассуждениями в <think>...</think>, поэтому ищем первый объект с
    ``required_key``."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL)
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and required_key in value:
            return value
    raise AiVisionError(f"не удалось разобрать ответ модели: {text.strip()[:200]!r}")


def parse_bbox_json(text):
    return parse_json_object(text, "found")


def _normalize_result(result):
    """Приводит поля ответа к числам: слабые модели присылают "12" или 12.5."""
    try:
        return {
            "found": bool(result.get("found")),
            "x": float(result.get("x") or 0),
            "y": float(result.get("y") or 0),
            "width": float(result.get("width") or 0),
            "height": float(result.get("height") or 0),
            "note": str(result.get("note") or ""),
        }
    except (TypeError, ValueError) as exc:
        raise AiVisionError(f"в ответе модели не числа: {result!r}") from exc


def user_turn(text, png=None):
    """Ход диалога для ``ask_json``/``stream_chat``: текст и (необязательно)
    PNG-картинка."""
    return {"role": "user", "text": text, "png": png}


def assistant_turn(text):
    return {"role": "assistant", "text": text, "png": None}


def _b64(png):
    return base64.standard_b64encode(png).decode("ascii")


# ---- Claude ----


def _anthropic_module():
    try:
        import anthropic
    except ImportError as exc:
        raise AiVisionError("не установлен пакет anthropic (pip install -r requirements.txt)") from exc
    return anthropic


def _anthropic_client(settings, client):
    if client is not None:
        return client
    anthropic = _anthropic_module()
    key = settings["anthropic_api_key"]
    return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()


def _anthropic_messages(turns):
    messages = []
    for turn in turns:
        content = []
        if turn["png"] is not None:
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _b64(turn["png"])}})
        content.append({"type": "text", "text": turn["text"]})
        messages.append({"role": turn["role"], "content": content})
    return messages


@contextmanager
def _anthropic_errors():
    anthropic = _anthropic_module()
    try:
        yield
    except anthropic.AuthenticationError as exc:
        raise AiVisionError("неверный ключ API Claude — проверь «Настройки» → «Настройки ИИ»") from exc
    except anthropic.RateLimitError as exc:
        raise AiVisionError("слишком много запросов, попробуй через минуту") from exc
    except anthropic.APIStatusError as exc:
        raise AiVisionError(f"ошибка API Claude ({exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise AiVisionError("нет соединения с API Claude — проверь интернет") from exc
    except TypeError as exc:
        # Клиент без ключа: SDK не нашёл ни настройки, ни ANTHROPIC_API_KEY.
        raise AiVisionError("не задан ключ API Claude — «Настройки» → «Настройки ИИ»") from exc


def _check_anthropic_stop(stop_reason):
    if stop_reason == "refusal":
        raise AiVisionError("модель отказалась выполнять запрос")
    if stop_reason == "max_tokens":
        raise AiVisionError("ответ модели оборвался, попробуй ещё раз")


def _anthropic_params(system, turns):
    return dict(
        model=ANTHROPIC_MODEL,
        system=system,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=_anthropic_messages(turns),
    )


def _ask_anthropic(system, turns, schema, settings, client):
    client = _anthropic_client(settings, client)
    with _anthropic_errors():
        response = client.beta.messages.create(
            max_tokens=16000,
            thinking={"type": "adaptive"},
            output_config={"format": {"type": "json_schema", "schema": schema}},
            **_anthropic_params(system, turns),
        )
    _check_anthropic_stop(response.stop_reason)
    return next((b.text for b in response.content if b.type == "text"), "")


def _stream_anthropic(system, turns, on_thinking, on_text, should_stop, settings, client):
    client = _anthropic_client(settings, client)
    text = []
    with _anthropic_errors():
        with client.beta.messages.stream(
            max_tokens=64000,
            # summarized — иначе у Claude текст рассуждений приходит пустым.
            thinking={"type": "adaptive", "display": "summarized"},
            **_anthropic_params(system, turns),
        ) as stream:
            for event in stream:
                if should_stop():
                    return "".join(text)
                if event.type != "content_block_delta":
                    continue
                if event.delta.type == "thinking_delta":
                    on_thinking(event.delta.thinking)
                elif event.delta.type == "text_delta":
                    text.append(event.delta.text)
                    on_text(event.delta.text)
            final = stream.get_final_message()
    _check_anthropic_stop(final.stop_reason)
    return "".join(text)


# ---- OpenAI-совместимые ----


def _openai_module():
    try:
        import openai
    except ImportError as exc:
        raise AiVisionError("не установлен пакет openai (pip install -r requirements.txt)") from exc
    return openai


def _openai_client(settings, client):
    if not settings["openai_model"]:
        raise AiVisionError("не задана модель — «Настройки» → «Настройки ИИ»")
    if client is not None:
        return client
    return _openai_module().OpenAI(
        base_url=settings["openai_base_url"] or None,
        # Локальным серверам ключ не нужен, но SDK без него не создаётся.
        api_key=settings["openai_api_key"] or "not-needed",
    )


def _openai_messages(system, turns):
    messages = [{"role": "system", "content": system}]
    for turn in turns:
        if turn["png"] is None:
            messages.append({"role": turn["role"], "content": turn["text"]})
            continue
        url = "data:image/png;base64," + _b64(turn["png"])
        messages.append(
            {
                "role": turn["role"],
                "content": [{"type": "image_url", "image_url": {"url": url}}, {"type": "text", "text": turn["text"]}],
            }
        )
    return messages


@contextmanager
def _openai_errors(model):
    openai = _openai_module()
    try:
        yield
    except openai.AuthenticationError as exc:
        raise AiVisionError("неверный ключ API — проверь «Настройки» → «Настройки ИИ»") from exc
    except openai.NotFoundError as exc:
        raise AiVisionError(f"сервер не знает модель «{model}» или адрес неверный: {exc.message}") from exc
    except openai.RateLimitError as exc:
        raise AiVisionError("слишком много запросов, попробуй через минуту") from exc
    except openai.APIStatusError as exc:
        raise AiVisionError(f"ошибка API ({exc.status_code}): {exc.message}") from exc
    except openai.APIConnectionError as exc:
        raise AiVisionError("нет соединения с сервером — проверь адрес и что он запущен") from exc


def _ask_openai(system, turns, schema, schema_name, settings, client):
    openai = _openai_module()
    client = _openai_client(settings, client)
    model = settings["openai_model"]
    messages = _openai_messages(system, turns)
    schema_format = {"type": "json_schema", "json_schema": {"name": schema_name, "schema": schema, "strict": True}}
    with _openai_errors(model):
        try:
            response = client.chat.completions.create(model=model, messages=messages, response_format=schema_format)
        except openai.BadRequestError:
            # Сервер не умеет structured output — просим JSON словами и
            # разбираем как получится.
            response = client.chat.completions.create(model=model, messages=messages)

    if not response.choices:
        raise AiVisionError("сервер вернул пустой ответ")
    message = response.choices[0].message
    if getattr(message, "refusal", None):
        raise AiVisionError(f"модель отказалась: {message.refusal}")
    return message.content or ""


class ThinkSplitter:
    """Делит поток текста на рассуждения и ответ по тегам <think>...</think>.

    Так рассуждения отдают Qwen/DeepSeek через LM Studio и Ollama, если
    сервер не выносит их в отдельное поле. Тег может прийти разрезанным
    между кусками потока («<thi» + «nk>»), поэтому хвост, похожий на начало
    тега, придерживается до следующего куска.
    """

    _OPEN, _CLOSE = "<think>", "</think>"

    def __init__(self):
        self._buffer = ""
        self._thinking = False

    def feed(self, chunk):
        """Returns: list[tuple[str, str]] — ("thinking" | "text", кусок)."""
        self._buffer += chunk
        out = []
        while True:
            tag = self._CLOSE if self._thinking else self._OPEN
            kind = "thinking" if self._thinking else "text"
            index = self._buffer.find(tag)
            if index >= 0:
                if index:
                    out.append((kind, self._buffer[:index]))
                self._buffer = self._buffer[index + len(tag) :]
                self._thinking = not self._thinking
                continue
            keep = next(
                (k for k in range(min(len(tag) - 1, len(self._buffer)), 0, -1) if tag.startswith(self._buffer[-k:])),
                0,
            )
            ready = self._buffer[: len(self._buffer) - keep]
            if ready:
                out.append((kind, ready))
            self._buffer = self._buffer[len(ready) :]
            return out

    def flush(self):
        rest, self._buffer = self._buffer, ""
        return [("thinking" if self._thinking else "text", rest)] if rest else []


def _stream_openai(system, turns, on_thinking, on_text, should_stop, settings, client):
    client = _openai_client(settings, client)
    model = settings["openai_model"]
    splitter = ThinkSplitter()
    text = []

    def route(parts):
        for kind, piece in parts:
            if kind == "thinking":
                on_thinking(piece)
            else:
                text.append(piece)
                on_text(piece)

    with _openai_errors(model):
        stream = client.chat.completions.create(model=model, messages=_openai_messages(system, turns), stream=True)
        try:
            for chunk in stream:
                if should_stop():
                    break
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                # Отдельное поле рассуждений: reasoning_content (LM Studio,
                # DeepSeek, vLLM) или reasoning (OpenRouter, Ollama).
                reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                if reasoning:
                    on_thinking(reasoning)
                if delta.content:
                    route(splitter.feed(delta.content))
        finally:
            close = getattr(stream, "close", None)
            if close is not None:
                close()
    route(splitter.flush())
    return "".join(text)


def resolve_settings(settings=None):
    return {**DEFAULT_SETTINGS, **(settings if settings is not None else get_ai_settings())}


def ask_json(system, turns, schema, schema_name, required_key, settings=None, client=None):
    """Один запрос к выбранному провайдеру с ответом-JSON по ``schema``.

    Блокирующий сетевой вызов — не вызывать из GUI-потока.

    Args:
        turns: ходы диалога (``user_turn``/``assistant_turn``), первый — user.
        required_key: поле, по которому JSON узнаётся в «грязном» ответе.

    Returns:
        tuple[dict, str]: разобранный объект и сырой текст ответа.
    """
    settings = resolve_settings(settings)
    if settings["provider"] == PROVIDER_OPENAI:
        text = _ask_openai(system, turns, schema, schema_name, settings, client)
    else:
        text = _ask_anthropic(system, turns, schema, settings, client)
    return parse_json_object(text, required_key), text


def stream_chat(system, turns, on_thinking, on_text, should_stop=lambda: False, settings=None, client=None):
    """Ответ модели в свободной форме, по кусочкам: рассуждения — в
    ``on_thinking``, текст ответа — в ``on_text`` (вызываются из того же
    потока, что и эта функция). ``should_stop()`` проверяется на каждом
    куске — так кнопка «Стоп» обрывает генерацию.

    Блокирующий сетевой вызов — не вызывать из GUI-потока.

    Returns:
        str: весь текст ответа (без рассуждений); при остановке — то, что успело прийти.
    """
    settings = resolve_settings(settings)
    stream = _stream_openai if settings["provider"] == PROVIDER_OPENAI else _stream_anthropic
    return stream(system, turns, on_thinking, on_text, should_stop, settings, client)


def encode_screenshot(bgr, settings=None):
    """Уменьшает скриншот под выбранного провайдера.

    Returns:
        tuple[bytes, float, int, int]: как у ``prepare_image``.
    """
    settings = resolve_settings(settings)
    if settings["provider"] == PROVIDER_OPENAI:
        side = max(256, int(settings["openai_max_side"]))
        return prepare_image(bgr, side, side * side)
    return prepare_image(bgr)


def locate_object(bgr, description, client=None, settings=None):
    """Спрашивает модель, где на скриншоте ``bgr`` находится ``description``.

    Блокирующий сетевой вызов (секунды, у локальных моделей — дольше) —
    не вызывать из GUI-потока.

    Args:
        client: готовый клиент SDK (для тестов); по умолчанию создаётся
            из настроек.
        settings: как ``get_ai_settings()``; по умолчанию читаются оттуда.

    Returns:
        tuple[tuple[int, int, int, int] | None, str]: рамка в пикселях
            скриншота (или None, если не нашла) и пояснение модели.
    """
    # Формат, определённый в режиме auto, запоминаем только для настроек
    # пользователя — не для явно переданных (тесты не пишут в QSettings).
    persist = settings is None
    settings = resolve_settings(settings)
    png, scale, sent_w, sent_h = encode_screenshot(bgr, settings)
    prompt = f"Найди на скриншоте: {description}"
    is_openai = settings["provider"] == PROVIDER_OPENAI
    mode = coords_mode(settings) if is_openai else COORDS_PIXELS
    if mode == COORDS_NORMALIZED:
        prompt += "\nКоординаты — в долях 0..1000 от ширины и высоты изображения."
    elif is_openai:
        prompt += f"\nИзображение {sent_w}×{sent_h} пикселей; координаты — в его пикселях, (0,0) — левый верхний угол."

    raw, _ = ask_json(_SYSTEM_PROMPT, [user_turn(prompt, png)], _BBOX_SCHEMA, "bbox", "found", settings, client)
    result = _normalize_result(raw)
    if not result["found"]:
        return None, result["note"]

    if mode == COORDS_AUTO:
        sent = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        mode, learned = _detect_coords(sent, result, description, settings, client)
        if mode is None:
            return None, "ИИ указал место, но не подтвердил, что объект там, — попробуй описать иначе"
        if learned and persist:
            set_ai_settings({"openai_coords_learned": f"{settings['openai_model']}|{mode}"})
    if mode == COORDS_NORMALIZED:
        result = _denormalize(result, sent_w, sent_h)

    h, w = bgr.shape[:2]
    return to_image_bbox(result, scale, w, h), result["note"]


def coords_mode(settings):
    """Формат координат OpenAI-совместимой модели: заданный в настройках
    или ранее определённый для этой модели; COORDS_AUTO — ещё неизвестен."""
    mode = settings["openai_coords"]
    if mode in (COORDS_PIXELS, COORDS_NORMALIZED):
        return mode
    model, _, learned = settings["openai_coords_learned"].partition("|")
    if model == settings["openai_model"] and learned in (COORDS_PIXELS, COORDS_NORMALIZED):
        return learned
    return COORDS_AUTO


def _denormalize(result, width, height):
    result = dict(result)
    for key, size in (("x", width), ("width", width), ("y", height), ("height", height)):
        result[key] = result[key] * size / 1000
    return result


def _box(result):
    return result["x"], result["y"], result["width"], result["height"]


def _iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


_CHOICE_PROMPT = """\
Ты проверяешь, правильно ли найден объект на скриншоте. Тебе покажут два \
увеличенных фрагмента скриншота: A (слева) и B (справа), на каждом красной \
рамкой обведено место. Ответь, на каком фрагменте внутри красной рамки \
находится описанный объект. Ответ — только JSON: {"choice": "A" | "B" | "none", \
"note": "одно короткое предложение по-русски"}."""

_CHOICE_SCHEMA = {
    "type": "object",
    "properties": {"choice": {"type": "string", "enum": ["A", "B", "none"]}, "note": {"type": "string"}},
    "required": ["choice", "note"],
    "additionalProperties": False,
}


def _detect_coords(sent, result, description, settings, client):
    """Определяет, в пикселях или в долях 0..1000 ответила модель.

    Returns:
        tuple[str | None, bool]: формат (None — модель не узнала объект ни
            в одном толковании) и можно ли его запомнить для этой модели.
    """
    height, width = sent.shape[:2]
    pixels = _box(result)
    normalized = _box(_denormalize(result, width, height))
    x, y, w, h = pixels
    if x + w > 1000 or y + h > 1000:
        return COORDS_PIXELS, True  # в долях 0..1000 таких чисел не бывает
    if x + w > width * 1.02 or y + h > height * 1.02:
        return COORDS_NORMALIZED, True  # в пикселях рамка вылезла бы за картинку
    if _iou(pixels, normalized) > 0.5:
        return COORDS_PIXELS, False  # картинка ~1000 px — толкования совпадают, узнать нельзя

    comparison = _comparison_image(sent, [pixels, normalized])
    ok, png = cv2.imencode(".png", comparison)
    if not ok:
        raise AiVisionError("не удалось подготовить картинку для проверки")
    answer, _ = ask_json(
        _CHOICE_PROMPT,
        [user_turn(f"Объект: {description}", png.tobytes())],
        _CHOICE_SCHEMA,
        "choice",
        "choice",
        settings,
        client,
    )
    choice = str(answer.get("choice", "")).strip().upper()
    if choice == "A":
        return COORDS_PIXELS, True
    if choice == "B":
        return COORDS_NORMALIZED, True
    return None, False


def _comparison_image(image, boxes, tile_height=320):
    """Фрагменты вокруг каждой рамки, с красной рамкой и подписью A/B/..., в ряд."""
    height, width = image.shape[:2]
    tiles = []
    for label, (x, y, w, h) in zip("ABCD", boxes):
        pad = int(max(w, h) * 2 + 30)
        x0, y0 = max(0, int(x) - pad), max(0, int(y) - pad)
        x1, y1 = min(width, int(x + w) + pad), min(height, int(y + h) + pad)
        crop = image[y0:y1, x0:x1].copy()
        if crop.size == 0:
            crop = np.zeros((tile_height, tile_height, 3), np.uint8)
        else:
            cv2.rectangle(crop, (int(x) - x0, int(y) - y0), (int(x + w) - x0, int(y + h) - y0), (0, 0, 255), 2)
        zoom = tile_height / crop.shape[0]
        crop = cv2.resize(crop, (max(1, int(crop.shape[1] * zoom)), tile_height), interpolation=cv2.INTER_NEAREST)
        banner = np.full((40, crop.shape[1], 3), 255, np.uint8)
        cv2.putText(banner, label, (10, 32), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 2)
        tiles.append(np.vstack([banner, crop]))
        tiles.append(np.full((tile_height + 40, 20, 3), 255, np.uint8))
    return np.hstack(tiles[:-1])


def count_matches(screen_bgr, template_bgr, threshold):
    """Сколько раз шаблон находится на скриншоте с оценкой >= threshold
    (соседние пиксели одного совпадения считаются одним). Нужна, чтобы сразу
    показать, узнаёт ли шаблон и другие такие же объекты, а не только тот,
    из которого вырезан."""
    th, tw = template_bgr.shape[:2]
    if screen_bgr.shape[0] < th or screen_bgr.shape[1] < tw:
        return 0
    result = cv2.matchTemplate(screen_bgr, template_bgr, cv2.TM_CCOEFF_NORMED)
    mask = (result >= threshold).astype(np.uint8)
    if not mask.any():
        return 0
    # Совпадения ближе половины шаблона друг к другу — одно и то же место.
    mask = cv2.dilate(mask, np.ones((max(1, th // 2), max(1, tw // 2)), np.uint8))
    count, _ = cv2.connectedComponents(mask)
    return count - 1
