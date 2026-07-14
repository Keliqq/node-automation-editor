# Node Automation Editor

A visual, node-graph editor for desktop automation — built with [PySide6](https://doc.qt.io/qtforpython/) and [NodeGraphQt](https://github.com/jchanvfx/NodeGraphQt). Wire up nodes for screen vision (image/pixel matching, OCR), mouse/keyboard actions, and control flow (if/else, while, wait) to automate anything on your desktop — not just games.

The UI is currently in Russian; node/category names are easy to translate if you want to contribute an English localization.

## Features

- **Visual exec graph** — connect nodes with exec pins; loops are just exec connections that loop back, no special "loop" runtime needed.
- **Screen vision** — find an image on screen, sample a pixel's color, or OCR text/numbers out of a screen region (via Tesseract).
- **Region picker** — drag-select a screen region directly instead of typing coordinates.
- **Mouse/keyboard actions** — click, move mouse, press a key, type text.
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

> `requirements.txt` pins `setuptools<81` — newer setuptools dropped `distutils`, which NodeGraphQt still imports on Python 3.12+/3.13.

## Built-in nodes

| Category | Node |
| --- | --- |
| События (Events) | Старт |
| Логика (Flow) | Если / Иначе, Цикл Пока, Ждать |
| Зрение (Vision) | Найти картинку на экране, Цвет пикселя, Прочитать текст с экрана (OCR) |
| Действия (Actions) | Клик, Переместить мышь, Нажать клавишу, Ввести текст |
| Отладка (Debug) | Вывести в консоль |

## Writing your own node

1. Add a `.py` file to `user_nodes/` (filenames starting with `_` are ignored).
2. Subclass `ExecNode` (has exec-in/exec-out pins, runs as a step in the flow) or `DataNode` (pure value, recomputed lazily when read).
3. Set `__identifier__` (controls which category it's grouped under in the node panel) and `NODE_NAME`.
4. In the app: "Добавить узел" → "Обновить список нод" to pick it up without restarting.

A broken user node file is logged and skipped — it won't crash the app.

## License

[MIT](LICENSE)
