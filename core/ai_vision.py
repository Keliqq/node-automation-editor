"""Поиск объекта на скриншоте по текстовому описанию через vision-модель.

Используется только при настройке графа — чтобы по описанию вроде «облако с
предметом над героем» вырезать шаблон для «Найти/Ждать картинку». Во время
работы графа ИИ не вызывается: там по-прежнему быстрый и бесплатный OpenCV.

Два провайдера (выбираются в «Настройки» → «Настройки ИИ»):
- Claude через SDK ``anthropic``;
- любой OpenAI-совместимый сервер через SDK ``openai``: OpenAI, OpenRouter,
  локальные LM Studio / Ollama / vLLM с vision-моделью (Qwen-VL и т.п.).

Модели по-разному отдают координаты: Claude — в пикселях присланной картинки
(в пределах своих лимитов), Qwen3-VL / Gemini — в долях 0..1000. Поэтому для
OpenAI-совместимых размер картинки прописывается в запросе явно, а
нормированные координаты включаются галочкой в настройках.
"""

import base64
import json
import re

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
    "openai_normalized": False,  # координаты 0..1000 (Qwen3-VL, Gemini)
}

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


def parse_bbox_json(text):
    """Достаёт JSON-ответ из текста модели. Локальные модели без поддержки
    structured output любят обернуть его в ```json или предварить
    рассуждениями в <think>...</think>, поэтому ищем первый объект с found."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL)
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "found" in value:
            return value
    raise AiVisionError(f"не удалось разобрать ответ модели: {text.strip()[:200]!r}")


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


def _ask_anthropic(png, description, settings, client):
    try:
        import anthropic
    except ImportError as exc:
        raise AiVisionError("не установлен пакет anthropic (pip install -r requirements.txt)") from exc

    if client is None:
        key = settings["anthropic_api_key"]
        client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=16000,
            system=_SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            output_config={"format": {"type": "json_schema", "schema": _BBOX_SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": base64.standard_b64encode(png).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": f"Найди на скриншоте: {description}"},
                    ],
                }
            ],
        )
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

    if response.stop_reason == "refusal":
        raise AiVisionError("модель отказалась обрабатывать этот скриншот")
    if response.stop_reason == "max_tokens":
        raise AiVisionError("ответ модели оборвался, попробуй ещё раз")
    return parse_bbox_json(next((b.text for b in response.content if b.type == "text"), ""))


def _ask_openai(png, description, settings, client, width, height):
    try:
        import openai
    except ImportError as exc:
        raise AiVisionError("не установлен пакет openai (pip install -r requirements.txt)") from exc

    model = settings["openai_model"]
    if not model:
        raise AiVisionError("не задана модель — «Настройки» → «Настройки ИИ»")
    if client is None:
        client = openai.OpenAI(
            base_url=settings["openai_base_url"] or None,
            # Локальным серверам ключ не нужен, но SDK без него не создаётся.
            api_key=settings["openai_api_key"] or "not-needed",
        )

    if settings["openai_normalized"]:
        units = "Координаты — в долях 0..1000 от ширины и высоты изображения."
    else:
        units = f"Изображение {width}×{height} пикселей; координаты — в его пикселях, (0,0) — левый верхний угол."
    request = dict(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64," + base64.standard_b64encode(png).decode("ascii")},
                    },
                    {"type": "text", "text": f"Найди на скриншоте: {description}\n{units}"},
                ],
            },
        ],
    )
    schema_format = {"type": "json_schema", "json_schema": {"name": "bbox", "schema": _BBOX_SCHEMA, "strict": True}}
    try:
        try:
            response = client.chat.completions.create(response_format=schema_format, **request)
        except openai.BadRequestError:
            # Сервер не умеет structured output — просим JSON словами и
            # разбираем как получится.
            response = client.chat.completions.create(**request)
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

    if not response.choices:
        raise AiVisionError("сервер вернул пустой ответ")
    message = response.choices[0].message
    if getattr(message, "refusal", None):
        raise AiVisionError(f"модель отказалась: {message.refusal}")
    result = _normalize_result(parse_bbox_json(message.content or ""))
    if settings["openai_normalized"]:
        for key, size in (("x", width), ("width", width), ("y", height), ("height", height)):
            result[key] = result[key] * size / 1000
    return result


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
    settings = {**DEFAULT_SETTINGS, **(settings if settings is not None else get_ai_settings())}
    if settings["provider"] == PROVIDER_OPENAI:
        side = max(256, int(settings["openai_max_side"]))
        png, scale, sent_w, sent_h = prepare_image(bgr, side, side * side)
        result = _ask_openai(png, description, settings, client, sent_w, sent_h)
    else:
        png, scale, _, _ = prepare_image(bgr)
        result = _normalize_result(_ask_anthropic(png, description, settings, client))

    if not result["found"]:
        return None, result["note"]
    h, w = bgr.shape[:2]
    return to_image_bbox(result, scale, w, h), result["note"]


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
