import base64
import json
from types import SimpleNamespace

import cv2
import numpy as np
import openai
import pytest

from core.ai_vision import (
    COORDS_AUTO,
    COORDS_NORMALIZED,
    COORDS_PIXELS,
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENAI,
    AiVisionError,
    ThinkSplitter,
    coords_mode,
    count_matches,
    locate_object,
    parse_bbox_json,
    prepare_image,
    stream_chat,
    to_image_bbox,
    user_turn,
)

# Настройки передаются явно: иначе тест прочитал бы QSettings разработчика.
ANTHROPIC = {"provider": PROVIDER_ANTHROPIC}
OPENAI = {"provider": PROVIDER_OPENAI, "openai_model": "test-vl", "openai_max_side": 1000, "openai_coords": COORDS_PIXELS}
OPENAI_AUTO = {**OPENAI, "openai_coords": COORDS_AUTO}


class FakeAnthropic:
    """Подменяет anthropic-клиент: запоминает запрос и отдаёт заранее
    заданный ответ, без сети и ключа."""

    def __init__(self, payload, stop_reason="end_turn"):
        self.requests = []
        self._response = SimpleNamespace(
            stop_reason=stop_reason,
            content=[SimpleNamespace(type="text", text=json.dumps(payload))],
        )
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self._response


class FakeOpenAI:
    """То же для openai-клиента. ``reject_schema`` изображает сервер без
    structured output: запрос с response_format отвергается с 400."""

    def __init__(self, *contents, reject_schema=False):
        self.requests = []
        self._contents = list(contents)
        self._reject_schema = reject_schema
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if self._reject_schema and "response_format" in kwargs:
            response = SimpleNamespace(status_code=400, headers={}, request=None)
            raise openai.BadRequestError("response_format not supported", response=response, body=None)
        # Ответы по очереди; последний повторяется.
        content = self._contents.pop(0) if len(self._contents) > 1 else self._contents[0]
        message = SimpleNamespace(content=content, refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _decode_anthropic_image(request):
    block = request["messages"][0]["content"][0]
    data = np.frombuffer(base64.standard_b64decode(block["source"]["data"]), np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _decode_openai_image(request):
    url = request["messages"][1]["content"][0]["image_url"]["url"]
    data = np.frombuffer(base64.standard_b64decode(url.split(",", 1)[1]), np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def test_prepare_image_keeps_small_screenshot_as_is():
    _, scale, w, h = prepare_image(np.zeros((1080, 1920, 3), np.uint8))
    assert (scale, w, h) == (1.0, 1920, 1080)


def test_prepare_image_downscales_large_screenshot_within_limits():
    png, scale, w, h = prepare_image(np.zeros((2160, 3840, 3), np.uint8))
    assert cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR).shape[:2] == (h, w)
    assert scale < 1.0
    assert max(w, h) <= 2576
    assert w * h <= 3_700_000


def test_to_image_bbox_scales_back_and_clamps():
    result = {"x": 50, "y": 25, "width": 20, "height": 10}
    assert to_image_bbox(result, 0.5, 1000, 1000) == (100, 50, 40, 20)
    # Рамка, вылезающая за край, обрезается по скриншоту.
    assert to_image_bbox({"x": 90, "y": 0, "width": 50, "height": 10}, 1.0, 100, 100) == (90, 0, 10, 10)
    assert to_image_bbox({"x": 200, "y": 0, "width": 5, "height": 5}, 1.0, 100, 100) is None


def test_anthropic_maps_coordinates_to_original_screenshot():
    screen = np.zeros((2160, 3840, 3), np.uint8)
    client = FakeAnthropic({"found": True, "x": 100, "y": 50, "width": 30, "height": 20, "note": "ok"})

    bbox, note = locate_object(screen, "облако", client=client, settings=ANTHROPIC)

    scale = _decode_anthropic_image(client.requests[0]).shape[1] / screen.shape[1]
    x0, y0 = round(100 / scale), round(50 / scale)
    assert bbox == (x0, y0, round(130 / scale) - x0, round(70 / scale) - y0)
    assert note == "ok"
    assert "облако" in client.requests[0]["messages"][0]["content"][1]["text"]


def test_anthropic_not_found_returns_none():
    client = FakeAnthropic({"found": False, "x": 0, "y": 0, "width": 0, "height": 0, "note": "нет облаков"})
    assert locate_object(np.zeros((100, 100, 3), np.uint8), "облако", client=client, settings=ANTHROPIC) == (
        None,
        "нет облаков",
    )


def test_anthropic_refusal_raises():
    client = FakeAnthropic({}, stop_reason="refusal")
    with pytest.raises(AiVisionError):
        locate_object(np.zeros((100, 100, 3), np.uint8), "облако", client=client, settings=ANTHROPIC)


def test_openai_downscales_to_max_side_and_states_size_in_prompt():
    screen = np.zeros((1000, 2000, 3), np.uint8)
    client = FakeOpenAI('{"found": true, "x": 100, "y": 50, "width": 20, "height": 10, "note": "ok"}')

    bbox, _ = locate_object(screen, "облако", client=client, settings=OPENAI)

    request = client.requests[0]
    assert _decode_openai_image(request).shape[:2] == (500, 1000)
    assert "1000×500" in request["messages"][1]["content"][1]["text"]
    assert request["response_format"]["type"] == "json_schema"
    assert bbox == (200, 100, 40, 20)


def test_openai_normalized_coordinates():
    screen = np.zeros((1000, 2000, 3), np.uint8)
    client = FakeOpenAI('{"found": true, "x": 500, "y": 100, "width": 10, "height": 20, "note": ""}')

    bbox, _ = locate_object(screen, "облако", client=client, settings={**OPENAI, "openai_coords": COORDS_NORMALIZED})

    assert bbox == (1000, 100, 20, 20)
    assert "0..1000" in client.requests[0]["messages"][1]["content"][1]["text"]


def bbox_json(x, y, w, h):
    return json.dumps({"found": True, "x": x, "y": y, "width": w, "height": h, "note": ""})


def test_auto_coords_decides_pixels_without_asking_when_numbers_exceed_1000():
    screen = np.zeros((1000, 3000, 3), np.uint8)  # уйдёт как 1500x500
    client = FakeOpenAI(bbox_json(1200, 10, 20, 10))
    bbox, _ = locate_object(screen, "x", client=client, settings={**OPENAI_AUTO, "openai_max_side": 1500})
    assert len(client.requests) == 1
    assert bbox == (2400, 20, 40, 20)


# Картинка уходит как 1000x500: по x оба толкования совпадают, по y — нет.
@pytest.mark.parametrize("choice, expected", [("A", (200, 100, 40, 20)), ("B", (200, 50, 40, 10))])
def test_auto_coords_asks_model_to_pick_between_readings(choice, expected):
    screen = np.zeros((1000, 2000, 3), np.uint8)  # уйдёт как 1000x500
    client = FakeOpenAI(bbox_json(100, 50, 20, 10), json.dumps({"choice": choice, "note": ""}))

    bbox, _ = locate_object(screen, "облако", client=client, settings=OPENAI_AUTO)

    assert len(client.requests) == 2
    comparison = _decode_openai_image(client.requests[1])
    assert comparison.shape[0] == 360  # два фрагмента A/B с подписями в ряд
    assert bbox == expected


def test_auto_coords_none_means_not_found():
    screen = np.zeros((1000, 2000, 3), np.uint8)
    client = FakeOpenAI(bbox_json(100, 50, 20, 10), json.dumps({"choice": "none", "note": ""}))
    bbox, note = locate_object(screen, "облако", client=client, settings=OPENAI_AUTO)
    assert bbox is None and note


def test_auto_coords_uses_mode_learned_for_this_model():
    screen = np.zeros((1000, 2000, 3), np.uint8)
    client = FakeOpenAI(bbox_json(100, 50, 20, 10))
    settings = {**OPENAI_AUTO, "openai_coords_learned": "test-vl|normalized"}

    bbox, _ = locate_object(screen, "облако", client=client, settings=settings)

    assert len(client.requests) == 1
    assert bbox == (200, 50, 40, 10)
    assert coords_mode({**settings, "openai_model": "другая"}) == COORDS_AUTO


def test_openai_retries_without_structured_output_and_parses_loose_json():
    reply = '<think>ищу облако...</think>Вот:\n```json\n{"found": true, "x": "10", "y": 20.4, "width": 30, "height": 40, "note": "хвостик"}\n```'
    client = FakeOpenAI(reply, reject_schema=True)

    bbox, note = locate_object(np.zeros((500, 500, 3), np.uint8), "облако", client=client, settings=OPENAI)

    assert len(client.requests) == 2
    assert "response_format" not in client.requests[1]
    assert bbox == (10, 20, 30, 40)
    assert note == "хвостик"


def test_openai_requires_model():
    with pytest.raises(AiVisionError):
        locate_object(np.zeros((100, 100, 3), np.uint8), "облако", client=FakeOpenAI(""), settings={**OPENAI, "openai_model": ""})


def test_parse_bbox_json_rejects_garbage():
    with pytest.raises(AiVisionError):
        parse_bbox_json("не вижу тут облаков")


@pytest.mark.parametrize("chunks", [["<think>думаю</think>ответ"], ["<thi", "nk>ду", "маю</th", "ink>отв", "ет"]])
def test_think_splitter_handles_tags_split_across_chunks(chunks):
    splitter = ThinkSplitter()
    parts = [part for chunk in chunks for part in splitter.feed(chunk)] + splitter.flush()
    joined = {kind: "".join(p for k, p in parts if k == kind) for kind in ("thinking", "text")}
    assert joined == {"thinking": "думаю", "text": "ответ"}


def test_think_splitter_passes_through_lone_angle_bracket():
    splitter = ThinkSplitter()
    parts = splitter.feed("x < 5 и y <") + splitter.flush()
    assert "".join(p for _, p in parts) == "x < 5 и y <"


class FakeOpenAIStream:
    """Потоковый ответ openai: куски с content и, по желанию, отдельным
    полем рассуждений reasoning_content (как у LM Studio)."""

    def __init__(self, deltas):
        self.requests = []
        self._deltas = deltas
        self.closed = False
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self

    def __iter__(self):
        for delta in self._deltas:
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(**delta))])

    def close(self):
        self.closed = True


def test_stream_chat_openai_routes_reasoning_and_think_tags():
    client = FakeOpenAIStream(
        [
            {"content": None, "reasoning_content": "поле "},
            {"content": "<think>тег</think>"},
            {"content": "Привет"},
            {"content": "!"},
        ]
    )
    thinking, text = [], []

    reply = stream_chat("sys", [user_turn("hi")], thinking.append, text.append, settings=OPENAI, client=client)

    assert reply == "Привет!"
    assert "".join(thinking) == "поле тег"
    assert "".join(text) == "Привет!"
    assert client.requests[0]["stream"] is True
    assert client.requests[0]["messages"][0] == {"role": "system", "content": "sys"}
    assert client.closed


def test_stream_chat_openai_stops_on_request():
    client = FakeOpenAIStream([{"content": "раз"}, {"content": "два"}])
    seen = []
    reply = stream_chat("s", [user_turn("hi")], seen.append, seen.append, lambda: bool(seen), OPENAI, client)
    assert reply == "раз"
    assert client.closed


def test_count_matches_counts_each_copy_once():
    rng = np.random.default_rng(0)
    template = rng.integers(0, 255, (12, 16, 3), dtype=np.uint8)
    screen = rng.integers(0, 255, (200, 300, 3), dtype=np.uint8)
    for x, y in ((10, 10), (150, 40), (60, 160)):
        screen[y : y + 12, x : x + 16] = template
    assert count_matches(screen, template, 0.9) == 3
