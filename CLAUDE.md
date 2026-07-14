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
```

There is no test suite yet — don't assume a `pytest`/`tox` command exists.

`requirements.txt` pins `setuptools<81`: newer setuptools dropped `distutils`, which the vendored NodeGraphQt still imports on Python 3.12+/3.13. Don't remove that pin.

Commit messages follow **Conventional Commits**: `type(scope): summary` (types: `feat`, `fix`, `refactor`, `docs`, `chore`, `test`, `perf`, `style`, `build`; scope is the touched area, e.g. `ui`, `ocr`, `core`, `nodes`; summary is imperative, lowercase, no trailing period).

## Architecture

This is a PySide6 + [NodeGraphQt](https://github.com/jchanvfx/NodeGraphQt) visual node-graph editor for desktop automation (screen vision, OCR, mouse/keyboard, control flow) — not limited to games despite some early naming. UI text and node labels are in Russian.

### Two node families (`core/exec_node.py`)

- **`ExecNode`** — imperative nodes with exec-in/exec-out pins (`add_exec_input`/`add_exec_output`), driven by `GraphEngine`. `execute(engine)` performs a side effect and returns the name of the exec output port to follow next (or `None` to stop that branch).
- **`DataNode`** — pure nodes with no exec pins (e.g. `FindImageOnScreenNode`, `ReadTextFromScreenNode`). `compute(engine)` is evaluated lazily, only when an `ExecNode` reads one of its outputs via `engine.get_input_value(...)`, and is cached per `(run_id, step_id)` so multiple reads within the same engine step see a consistent value instead of re-sampling the screen each time.

Both share `add_data_input`/`add_data_output`, which back a node property (shown in the "Свойства" panel) *and* an input port — `engine.get_input_value(node, port)` (`core/engine.py`) falls back to the node's own property when the port isn't wired, so a data input works both as a literal and as a connectable value.

### Execution engine (`core/engine.py`)

`GraphEngine` is a `QThread` so long automation runs don't freeze the GUI and "Остановить" can interrupt between steps. `run()` finds every `StartNode` on the graph and walks each one's exec-output connections node-by-node (`_run_chain`) until a dead end. **Loops are just cyclic exec wiring** (e.g. `WhileLoopNode`'s "тело_цикла" output connected back upstream) — the engine has no special loop construct, it only ever follows one exec connection at a time.

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
- `core/ocr.py` — resolves the `tesseract.exe` path (user override via `QSettings`, else default install locations) and upscales small HUD-sized text crops before running Tesseract, since it needs roughly 30px-tall glyphs to read reliably.

### NodeGraphQt compatibility patches

`core/nodegraphqt_patches.py` must be imported before any `PropertiesBinWidget` or node is created (done at the top of `ui/main_window.py`) — it monkey-patches the vendored NodeGraphQt for newer PySide6:

- `PropSpinBox`/`PropDoubleSpinBox` reference a `NoButtons` enum alias PySide6 6.11 removed.
- `BackdropNode` lacks an `icon()` method the properties panel calls unconditionally.

Separately, `core/exec_node.py`'s `_WIDGET_BY_DTYPE` maps `int`/`float` properties to NodeGraphQt's `INT`/`FLOAT` (plain line-edit) widgets, **not** `QSPIN_BOX`/`QDOUBLESPIN_BOX` — those spin widgets are constructed with no explicit range, so they silently clamp any value to Qt's default `0..99` and write the clamped value straight back into the node property. Keep this in mind if you add a new numeric input type.

Also note: in PySide6/Qt6, standard shortcuts are `QKeySequence.StandardKey.New` etc. (nested enum), not `QKeySequence.New` directly.
