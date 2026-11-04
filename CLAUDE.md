# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# setup (Windows)
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# run
python main.py

# tests (offscreen Qt, no window)
pip install -r requirements-dev.txt
python -m pytest
python -m pytest tests/test_engine.py::test_function_call_returns_to_caller   # single test
```

Tests build a real `NodeGraph` under `QT_QPA_PLATFORM=offscreen` (`tests/conftest.py`) with a few test-only nodes (`RecordNode` appends to `engine.records`) and call `engine.run()` synchronously, or `engine.start()` + polling for threaded/pause tests. Never add tests that execute real `Клик`/`Ввести текст` without monkeypatching `pyautogui`/`keyboard` — they'd drive the developer's actual mouse and keyboard.

`requirements.txt` pins `setuptools<81`: newer setuptools dropped `distutils`, which the vendored NodeGraphQt still imports on Python 3.12+/3.13. Don't remove that pin.

Commit messages follow **Conventional Commits**: `type(scope): summary` (types: `feat`, `fix`, `refactor`, `docs`, `chore`, `test`, `perf`, `style`, `build`; scope is the touched area, e.g. `ui`, `ocr`, `core`, `nodes`; summary is imperative, lowercase, no trailing period).

## Architecture

This is a PySide6 + [NodeGraphQt](https://github.com/jchanvfx/NodeGraphQt) visual node-graph editor for desktop automation (screen vision, OCR, mouse/keyboard, control flow) — not limited to games despite some early naming. UI text and node labels are in Russian.

### Two node families (`core/exec_node.py`)

- **`ExecNode`** — imperative nodes with exec-in/exec-out pins (`add_exec_input`/`add_exec_output`), driven by `GraphEngine`. `execute(engine)` performs a side effect and returns the name of the exec output port to follow next (or `None` to stop that branch).
- **`DataNode`** — pure nodes with no exec pins (e.g. `FindImageOnScreenNode`, `ReadTextFromScreenNode`). `compute(engine)` is evaluated lazily, only when an `ExecNode` reads one of its outputs via `engine.get_input_value(...)`, and is cached per `(run_id, step_id)` so multiple reads within the same engine step see a consistent value instead of re-sampling the screen each time.

Both share `add_data_input`/`add_data_output`, which back a node property (shown in the "Свойства" panel) *and* an input port — `engine.get_input_value(node, port)` (`core/engine.py`) falls back to the node's own property when the port isn't wired, so a data input works both as a literal and as a connectable value. Read inputs via the engine, not `self.get_property(...)`, or a wired value is silently ignored.

Convert input values with `core/values.py` (`to_int`/`to_float`/`to_bool`), never `int(value or default)`: that pattern replaces a deliberate `0` with the default and turns a missing value (`None` from "Найти картинку" that found nothing) into `0` — which once made "Клик" hit the screen corner. `to_int(value)` without a default raises `MissingValueError`, which the engine logs and uses to stop the branch.

Nodes with state that must not leak between runs (e.g. `WhileLoopNode._iterations`) override `reset_runtime()` and call `super()`; the engine calls it on every node at the start of a run. Every `ExecNode` also gets a `точка_останова` checkbox property (tab "Отладка", `BREAKPOINT_PROP` in `core/ports.py`).

### Execution engine (`core/engine.py`)

`GraphEngine` is a `QThread` so long automation runs don't freeze the GUI and "Остановить" can interrupt between steps. It is **one-shot**: a new engine is created per run, and its constructor (running on the GUI thread) snapshots all wiring into plain dicts (`_exec_next`, `_data_src`) plus the `FunctionNode` name table. The worker thread only walks that snapshot, never live NodeGraphQt ports, so the user can edit wires mid-run without racing the engine (edits apply from the next run). Property *values* are still read live. `run()` deliberately doesn't clear `_stop_event` — a stop issued before the thread starts must not be lost.

Each `StartNode` is its own chain; with more than one, each runs on its own `threading.Thread` (inside the `QThread`) so an endless loop in one doesn't starve the others. Per-chain state (`chain_id`, `step_id`, call depth) lives in `threading.local`; `DataNode` caches per chain via `engine.cache_key()`. The only cross-chain state is variables (`get_variable`/`set_variable`/`update_variable`, lock-protected, scoped to one run). **Loops are just cyclic exec wiring** (e.g. `WhileLoopNode`'s "тело_цикла" output connected back upstream) — the engine has no special loop construct. **Functions** are the same idea: `CallFunctionNode` → `engine.call_function(name)` runs `_run_chain` from the matching `FunctionNode` synchronously and returns (depth-limited by `MAX_CALL_DEPTH`).

Pause/step/breakpoints: before executing each node `_wait_if_paused` blocks on a `threading.Condition` while paused (set by `pause()`, `start_paused=True`, or the node's `точка_останова`); `step()` grants a one-node budget, `resume()` clears the pause, `stop()` wakes waiters. The engine emits `paused(node_id)`/`resumed()` for the toolbar. In the UI, run highlighting and the red breakpoint border use `set_property(..., push_undo=False)` — `node.set_color` pushes to the undo stack, which would pollute "Отменить" and mark the tab modified on every run. The tab's "modified" state (`*` in the title, save prompt on close) is just `not undo_stack().isClean()`.

### Tabs (`ui/main_window.py`)

`MainWindow` hosts a `QTabWidget`; each tab is a fully independent `NodeGraph` wrapped in a `GraphTab` (own `GraphEngine`, `session_path`, run-highlight colors). Two NodeGraphQt widgets are bound per-instance, which shapes how tabs are implemented:

- `PropertiesBinWidget` permanently wires itself to one `NodeGraph` (`node_graph.add_properties_bin(self)`), so each tab gets its own instance, swapped into view via a `QStackedWidget` on tab change.
- `NodesTreeWidget` (the left node palette) only reads `node_graph.node_factory.names` to populate drag data — since every graph registers the same `ALL_NODES`, a single shared instance works for every tab; drag-and-drop lands on whichever graph widget is currently visible regardless of which graph built the tree.
- Tab state is tracked in `MainWindow._tab_data`, a `dict` keyed by `graph.widget` (not an attribute stashed on the widget — `NodeGraphWidget` is a third-party class and static type checkers reject dynamic attributes on it).

### Node registration & the plugin system

`nodes/__init__.py` builds `ALL_NODES = BUILTIN_NODES + discover_user_nodes()`. This list is **mutated in place** (`.extend(...)`), never rebound — every module that did `from nodes import ALL_NODES` (`ui/main_window.py`) must keep seeing the same list object when new nodes are hot-loaded.

`core/node_loader.py` scans `user_nodes/*.py` (files starting with `_` are skipped), imports each file independently via `importlib`, and collects classes subclassing `ExecNode`/`DataNode` that are (a) declared directly in that file (`__module__` check, not just imported into it) and (b) have both `__identifier__` and `NODE_NAME` set. A broken user file is logged and skipped, not fatal — see `user_nodes/example_beep_node.py` for the expected shape of a plugin node.

"Добавить узел" → "Обновить список нод" in the app re-runs discovery, extends `ALL_NODES`, registers only the newly-found classes into every currently-open tab's graph (`register_nodes` raises if a type is already registered, so already-known nodes must be filtered out first), and rebuilds the node palette/menu — all without restarting the app.

### Emergency-stop hotkey (`core/hotkeys.py`)

A global hotkey (default `F9`, stored via `QSettings`, changeable in Настройки) stops every running tab's engine at once, even when the app isn't focused — the whole point of a panic button, since the automation itself may be driving the mouse/keyboard over some other window. The `keyboard` library's hook callback runs on its own non-Qt thread, so the callback is just `emergency_stop_requested.emit()`; the actual stop logic (`MainWindow._stop_all_engines`) runs on the GUI thread via the normal cross-thread signal/slot queued connection — don't touch widgets or `_tab_data` directly from the hotkey callback. Windows UIPI means this can't see keypresses over a higher-privileged (elevated) foreground window unless the editor is elevated too.

### App root resolution / PyInstaller build (`core/app_paths.py`, `node_editor.spec`)

`APP_ROOT` is the repo root when running from source, but `os.path.dirname(sys.executable)` when frozen (`sys.frozen`) — `ui/main_window.py`'s `EXAMPLES_DIR` and `core/node_loader.py`'s `USER_NODES_DIR` are both built from it. Don't compute these via `__file__` directly in a frozen build: PyInstaller onefile extracts to a throwaway temp dir (`sys._MEIPASS`) that's wiped after the process exits, which would make `user_nodes/`/`examples/` invisible between runs and impossible for a user to actually edit.

Build with `pyinstaller --noconfirm node_editor.spec` (onedir, not onefile — so `examples/`/`user_nodes/` can be copied next to the `.exe` as plain, user-editable folders afterward; see README "Building a standalone .exe"). The spec adds `PySide6.QtSvg`/`QtSvgWidgets` as hidden imports and NodeGraphQt's `widgets/icons/node_base.png` as data — both are only reachable through NodeGraphQt's dynamic `Qt.py` binding shim, which PyInstaller's static analysis doesn't see, so without them the frozen exe fails at import time (`ImportError: cannot import name 'QtSvg' from 'Qt'`) or renders without the base node icon.

### Screen/vision layer

- `core/screen.py` — `mss`-based capture; monitor coordinates can be negative (multi-monitor virtual desktop), so don't assume `x`/`y` >= 0.
- `core/region_selector.py` — fullscreen overlay used by the OCR node's "Выбрать на экране" button to drag-select a region and feed `x`/`y`/width/height back into the node's properties.
- `core/ai_vision.py` + `core/ai_template_dialog.py` — "Создать с помощью ИИ..." on the image-search nodes. Setup-time only (never called from `GraphEngine`): hides the editor windows, screenshots the node's monitor, asks a vision model for a bounding box, lets the user nudge it in a preview, saves the PNG under `templates/`. The API call runs on a plain thread so the GUI stays responsive. Two providers, chosen in Настройки → Настройки ИИ (`QSettings` keys under `ai/`): Claude via the `anthropic` SDK (`claude-opus-5`, JSON-schema structured output; screenshot downscaled to its 2576 px / ~3.7 MP limit, where its coordinates are 1:1 with pixels), or any OpenAI-compatible server via the `openai` SDK (OpenAI, OpenRouter, LM Studio, Ollama). Models disagree on coordinate conventions, so the OpenAI path downscales to a configurable max side, states the exact image size in the prompt, and handles the coordinate convention per `openai_coords` (`pixels` / `normalized` 0..1000 as Qwen3-VL and Gemini are trained to answer regardless of the prompt / `auto`). In `auto`, `_detect_coords` settles it from the numbers when it can (a value > 1000 ⇒ pixels; a pixel box off the image ⇒ normalized), otherwise sends the model a side-by-side of both readings ("A or B?") and remembers the answer per model in `openai_coords_learned` — only when `locate_object` read settings itself, so tests passing `settings=` never write `QSettings`. The template preview also shows the whole screenshot (`_ScreenOverview`), where the user can drag-select the object when the model misses entirely. It falls back to no `response_format` if the server 400s on it, and `parse_bbox_json` digs the JSON out of ```-fences / `<think>` blocks. `tests/test_ai_vision.py` uses fake clients and always passes `settings=` explicitly so it never reads the developer's `QSettings` or hits the network.
- `core/ai_graph.py` + `core/ai_chat.py` — the "ИИ-ассистент" dock: a chat in which the model builds a graph. Protocol is free text + the plan as a ```json block in the reply (no tool calling / structured output, so it works with local OpenAI-compatible models too); `extract_plan` pulls the last such block and normalizes ids to strings. `GraphChat` (Qt-free, unit-tested with fake streaming clients) keeps the turns, attaches screenshots, and validates each plan; `AiChatWidget` runs `GraphChat.send` on a plain thread, streams text/reasoning into the bubble via a `_Relay` QObject's signals (a generation counter drops late signals after "Новый чат"), auto-sends plan errors back up to `MAX_AUTO_FIXES` times, and hands a valid plan to `MainWindow._build_ai_graph`, which rebuilds the chat's own tab in one undo macro. Streaming lives in `core/ai_vision.py` `stream_chat`: Claude via `beta.messages.stream` with `thinking.display="summarized"` (otherwise its thinking text is empty); OpenAI-compatible via `stream=True`, reasoning from `delta.reasoning_content`/`delta.reasoning` or from `<think>` tags split out by `ThinkSplitter` (tags can arrive cut across chunks). Don't use `graph.select_all()`/`clear_selection()` around builds — NodeGraphQt pushes selection changes onto the undo stack; nodes are created with `selected=False` so `fit_to_selection()` frames everything. `build_catalog` instantiates every node class in a throwaway `NodeGraph` to read its real ports (exec vs data told apart by `EXEC_PORT_COLOR`), properties, combo items and value kind (from the property's widget type, not its default — `CompareNode` has `default=0` on a float field); `catalog_text` turns that into the model's system prompt, so new/plugin nodes need no prompt changes, just a good first docstring paragraph. A plan names nodes by `NODE_NAME`, gives properties as strings, lists connections, and gives image nodes a `template` *description* — never a path. `validate_plan` first checks the shape (the plan is free-form JSON), then rejects unknown nodes/ports/properties, exec↔data wires, a second wire on an exec output / data input, unreachable exec nodes, a missing «Старт». `apply_plan` builds the graph as a single undo step using `layout_plan` — not `NodeGraph.auto_layout_nodes`, which recurses forever on cycles (and every loop here is a cycle). Shared helpers for this and the template flow (`ask_json`, `stream_chat`, `grab_screen_hidden`, `template_from_screen`) live in `core/ai_vision.py` / `core/ai_template_dialog.py`.
- `core/ocr.py` — resolves the `tesseract.exe` path (user override via `QSettings`, else default install locations) and upscales small HUD-sized text crops before running Tesseract, since it needs roughly 30px-tall glyphs to read reliably.

### NodeGraphQt compatibility patches

`core/nodegraphqt_patches.py` must be imported before any `PropertiesBinWidget` or node is created (done at the top of `ui/main_window.py`) — it monkey-patches the vendored NodeGraphQt for newer PySide6:

- `PropSpinBox`/`PropDoubleSpinBox` reference a `NoButtons` enum alias PySide6 6.11 removed.
- `BackdropNode` lacks an `icon()` method the properties panel calls unconditionally.

Separately, `core/exec_node.py`'s `_WIDGET_BY_DTYPE` maps `int`/`float` properties to NodeGraphQt's `INT`/`FLOAT` (plain line-edit) widgets, **not** `QSPIN_BOX`/`QDOUBLESPIN_BOX` — those spin widgets are constructed with no explicit range, so they silently clamp any value to Qt's default `0..99` and write the clamped value straight back into the node property. Keep this in mind if you add a new numeric input type.

Also note: in PySide6/Qt6, standard shortcuts are `QKeySequence.StandardKey.New` etc. (nested enum), not `QKeySequence.New` directly.
