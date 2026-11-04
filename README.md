# Node Automation Editor

A visual, node-graph editor for desktop automation — built with [PySide6](https://doc.qt.io/qtforpython/) and [NodeGraphQt](https://github.com/jchanvfx/NodeGraphQt). Wire up nodes for screen vision (image/pixel matching, OCR), mouse/keyboard actions, and control flow (if/else, while, wait) to automate anything on your desktop — not just games.

The UI is currently in Russian; node/category names are easy to translate if you want to contribute an English localization.

## Features

- **Visual exec graph** — connect nodes with exec pins; loops are just exec connections that loop back, no special "loop" runtime needed.
- **Parallel start chains** — every "Старт" node runs as its own concurrent chain, so an endless loop in one doesn't block the others.
- **Variables & functions** — set/increment/read run-scoped variables shared by all chains; wrap a reusable chain in a "Функция" node and run it from anywhere with "Вызвать функцию".
- **Debugger** — pause, step one node at a time (⏭ also starts a run in step mode), and toggle breakpoints on selected nodes with `F8` (red border).
- **Screen vision** — find an image on screen (optionally restricted to a region, grayscale for speed), wait for an image with a timeout, sample a pixel's color, or OCR text/numbers out of a screen region (via Tesseract).
- **AI template maker** — "Создать с помощью ИИ..." on the image-search nodes: describe the object in words, the app takes a screenshot, a vision model finds the object, you adjust the box in a preview and save the PNG template. Works with Claude (Anthropic API key) or any OpenAI-compatible server — OpenAI, OpenRouter, or a local LM Studio/Ollama vision model (Настройки → Настройки ИИ). Only used at setup time — the running graph still matches with plain OpenCV.
- **AI assistant** — the "ИИ-ассистент" panel (also "Добавить узел" → "ИИ-ассистент: собрать граф..."): chat with the model about what the automation should do. It sees an auto-generated catalog of all nodes (plugin nodes included) and, optionally, a screenshot; it asks clarifying questions when the task is ambiguous, streams its answer and its reasoning live, and proposes a graph plan that's validated before anything is built (errors go back to the model automatically). "Построить граф" cuts the template images from the screenshot and builds the graph in its own tab — as one undo step, and rebuilding after "change X" replaces it. Nothing is ever started automatically.
- **Region picker** — drag-select a screen region directly instead of typing coordinates.
- **Mouse/keyboard actions** — click, move mouse, press a key, type text (including Cyrillic). A click with no coordinates (e.g. the image wasn't found) stops the branch instead of clicking the screen corner.
- **Unsaved-changes guard** — modified tabs are marked with `*` and you're asked to save before closing.
- **Tabs** — each open graph is its own tab (own undo stack, own run engine), like a browser.
- **Examples menu** — anything dropped into `examples/` shows up in the app's "Примеры" menu, ready to open.
- **Plugin nodes** — drop a `.py` file into `user_nodes/` with a class inheriting `ExecNode`/`DataNode`, then hit "Обновить список нод" — no rebuild, no editing the core codebase. See `user_nodes/example_beep_node.py` for a template.
- **Emergency-stop hotkey** — global hotkey (default `F9`, changeable via Настройки → "Хоткей аварийной остановки...") that stops every running graph in every tab, even while the app doesn't have focus — useful since the automation itself is moving your mouse/keyboard.

## Requirements

- Python 3.12+
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) installed, if you want to use the "Прочитать текст с экрана" (OCR) node. On Windows it's auto-detected in the default install location; otherwise set the path via the app's Настройки → "Путь к Tesseract OCR..." menu.
- The OCR node reads with `lang="rus+eng"`, so Tesseract's Russian language data (`rus.traineddata`) needs to be installed alongside the default `eng` one, or the node will log a language-not-found error instead of recognizing text.
- If the window/process you're automating runs elevated (as Administrator) and the editor doesn't, Windows won't let the emergency-stop hotkey see keypresses over it (UIPI) — run the editor elevated too in that case.

## Setup

```bash
python -m venv venv
venv\Scripts\activate      # Windows
pip install -r requirements.txt
python main.py
```

Tests (engine, flow, variables, functions, pause/step — no real mouse/screen needed except where noted):

```bash
pip install -r requirements-dev.txt
python -m pytest
```

> `requirements.txt` pins `setuptools<81` — newer setuptools dropped `distutils`, which NodeGraphQt still imports on Python 3.12+/3.13.

## Built-in nodes

| Category | Node |
| --- | --- |
| События (Events) | Старт, Функция, Вызвать функцию |
| Логика (Flow) | Сравнить числа, Если / Иначе, Цикл Пока, Ждать |
| Переменные (Variables) | Задать переменную, Увеличить переменную, Получить переменную |
| Зрение (Vision) | Найти картинку на экране, Ждать картинку, Цвет пикселя, Прочитать текст с экрана (OCR) |
| Действия (Actions) | Клик, Переместить мышь, Нажать клавишу, Ввести текст |
| Отладка (Debug) | Вывести в консоль |

## Writing your own node

1. Add a `.py` file to `user_nodes/` (filenames starting with `_` are ignored).
2. Subclass `ExecNode` (has exec-in/exec-out pins, runs as a step in the flow) or `DataNode` (pure value, recomputed lazily when read).
3. Set `__identifier__` (controls which category it's grouped under in the node panel) and `NODE_NAME`.
   Read inputs with `engine.get_input_value(...)` and convert them with `core.values.to_int`/`to_float`/`to_bool` rather than `int(value or 0)`, so a real `0` isn't replaced by a default and a missing value isn't silently treated as `0`. If your node keeps state between executions, override `reset_runtime()`.
4. In the app: "Добавить узел" → "Обновить список нод" to pick it up without restarting.

A broken user node file is logged and skipped — it won't crash the app.

## Building a standalone .exe

To hand the app to someone without Python installed:

```bash
pip install -r requirements-build.txt
pyinstaller --noconfirm node_editor.spec
xcopy /E /I examples dist\NodeAutomationEditor\examples
xcopy /E /I user_nodes dist\NodeAutomationEditor\user_nodes
```

`dist\NodeAutomationEditor\` is the whole thing to hand over — `examples/` and `user_nodes/` sit next to the `.exe` there and stay user-editable (drop a plugin `.py` right in), unlike PyInstaller's own bundled `datas`, which live in a location that's awkward to reach and gets recreated every build. Tesseract OCR still needs to be installed separately on the target machine (see Requirements above) — PyInstaller only bundles the Python side.

## License

[MIT](LICENSE)
