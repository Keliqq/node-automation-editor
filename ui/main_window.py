import os
from typing import Optional

from NodeGraphQt import NodeGraph, NodesTreeWidget, PropertiesBinWidget
from PySide6 import QtCore, QtGui, QtWidgets

import core.nodegraphqt_patches  # noqa: F401  чинит числовые виджеты NodeGraphQt до их использования
from core.app_paths import APP_ROOT
from core.engine import GraphEngine
from core.ai_graph import apply_plan
from core.ai_chat import AiChatWidget
from core.ai_template_dialog import edit_ai_settings
from core.hotkeys import HotkeyManager, get_stop_hotkey, set_stop_hotkey
from core.exec_node import ExecNode
from core.ocr import get_tesseract_path, set_tesseract_path
from core.ports import BREAKPOINT_PROP
from nodes import ALL_NODES

RUNNING_COLOR = (240, 200, 60)
BREAKPOINT_BORDER_COLOR = (255, 50, 50, 255)
DEFAULT_BORDER_COLOR = (74, 84, 85, 255)  # как у NodeGraphQt по умолчанию

EXAMPLES_DIR = os.path.join(APP_ROOT, "examples")

CATEGORY_LABELS = {
    "automation.events": "События",
    "automation.flow": "Логика",
    "automation.variables": "Переменные",
    "automation.vision": "Зрение",
    "automation.actions": "Действия",
    "automation.debug": "Отладка",
}


class GraphTab:
    """Состояние одной вкладки: свой граф, своя панель свойств, свой движок."""

    def __init__(self, graph, properties_bin, title):
        self.graph = graph
        self.properties_bin = properties_bin
        self.title = title
        self.engine: Optional[GraphEngine] = None
        self.session_path: Optional[str] = None
        self.node_colors = {}

    def is_modified(self):
        return not self.graph.undo_stack().isClean()

    def is_running(self):
        return bool(self.engine and self.engine.isRunning())


class MainWindow(QtWidgets.QMainWindow):
    emergency_stop_requested = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Node Automation Editor")
        self.resize(1400, 900)

        self._next_node_pos = [300, 0]
        self._new_tab_counter = 0
        self.nodes_tree = None
        self._tab_data = {}  # graph.widget -> GraphTab; словарь вместо атрибута на виджете,
        # т.к. NodeGraphWidget — чужой класс без объявленного поля tab_data

        self._build_ui()
        self._build_menu()
        self.new_graph()

        # Хоткей ставится колбэком, который зовёт keyboard из СВОЕГО потока —
        # поэтому колбэк только эмитит сигнал, а реальная остановка (_stop_all_engines)
        # выполняется в GUI-потоке через обычное подключение сигнал/слот.
        self._hotkey_manager = HotkeyManager(self.emergency_stop_requested.emit)
        self.emergency_stop_requested.connect(self._stop_all_engines)
        self._apply_stop_hotkey(get_stop_hotkey())

    # ---- построение интерфейса ----
    def _build_ui(self):
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)

        palette_dock = QtWidgets.QDockWidget("Ноды (перетащи на холст)", self)
        palette_dock.setObjectName("palette_dock")
        self.palette_dock = palette_dock
        self.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, palette_dock)

        self.properties_stack = QtWidgets.QStackedWidget()
        properties_dock = QtWidgets.QDockWidget("Свойства", self)
        properties_dock.setObjectName("properties_dock")
        properties_dock.setWidget(self.properties_stack)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, properties_dock)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        log_dock = QtWidgets.QDockWidget("Лог", self)
        log_dock.setObjectName("log_dock")
        log_dock.setWidget(self.log_view)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, log_dock)

        self.ai_chat = AiChatWidget(lambda: ALL_NODES, self._build_ai_graph)
        ai_dock = QtWidgets.QDockWidget("ИИ-ассистент", self)
        ai_dock.setObjectName("ai_dock")
        ai_dock.setWidget(self.ai_chat)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, ai_dock)
        self.tabifyDockWidget(properties_dock, ai_dock)
        properties_dock.raise_()
        self.ai_dock = ai_dock
        self._ai_tab = None  # вкладка, куда чат ИИ строит граф

        self._docks = [palette_dock, properties_dock, ai_dock, log_dock]

        toolbar = QtWidgets.QToolBar("Выполнение")
        self.addToolBar(toolbar)

        self.run_action = toolbar.addAction("▶ Запустить")
        self.run_action.triggered.connect(self.start_run)

        self.pause_action = toolbar.addAction("⏸ Пауза")
        self.pause_action.triggered.connect(self.pause_run)

        self.step_action = toolbar.addAction("⏭ Шаг")
        self.step_action.setToolTip("Выполнить одну ноду и остановиться (если не запущено — запуск по шагам)")
        self.step_action.triggered.connect(self.step_run)

        self.stop_action = toolbar.addAction("⏹ Остановить")
        self.stop_action.triggered.connect(self.stop_run)

        self._sync_run_actions()

        self.status_label = QtWidgets.QLabel("Готово")
        self.statusBar().addPermanentWidget(self.status_label)

        next_tab_action = QtGui.QAction(self)
        next_tab_action.setShortcut("Ctrl+Tab")
        next_tab_action.triggered.connect(lambda: self._step_tab(1))
        self.addAction(next_tab_action)

        prev_tab_action = QtGui.QAction(self)
        prev_tab_action.setShortcut("Ctrl+Shift+Tab")
        prev_tab_action.triggered.connect(lambda: self._step_tab(-1))
        self.addAction(prev_tab_action)

    def _remove_tree_category(self, tree, category_label):
        for i in reversed(range(tree.topLevelItemCount())):
            item = tree.topLevelItem(i)
            if item is not None and item.text(0) == category_label:
                tree.takeTopLevelItem(i)

    def _ensure_nodes_tree(self, graph):
        """Список нод одинаков для любого графа, поэтому дерево строится один
        раз (по фактории первого графа) и используется всеми вкладками —
        перетаскивание ноды всё равно попадает на холст активной вкладки."""
        if self.nodes_tree is not None:
            return
        tree = NodesTreeWidget(node_graph=graph)
        for category, label in CATEGORY_LABELS.items():
            tree.set_category_label(category, label)
        self._remove_tree_category(tree, "nodeGraphQt.nodes")  # встроенная нода Backdrop, здесь не нужна
        self.palette_dock.setWidget(tree)
        self.nodes_tree = tree

    def _make_action(self, parent, text, slot, shortcut=None):
        """QMenu.addAction(text, slot) неоднозначно типизирован в стабах
        PySide6 (может резолвиться в перегрузку, возвращающую None), из-за
        чего статический анализатор ругается на .setShortcut(...) ниже.
        Создаём QAction явно, чтобы тип результата был однозначным."""
        action = QtGui.QAction(text, parent)
        action.triggered.connect(slot)
        if shortcut is not None:
            action.setShortcut(shortcut)
        parent.addAction(action)
        return action

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("Файл")

        StandardKey = QtGui.QKeySequence.StandardKey
        self._make_action(file_menu, "Новый граф", self.new_graph, StandardKey.New)
        self._make_action(file_menu, "Открыть...", lambda: self.open_graph(), StandardKey.Open)
        self._make_action(file_menu, "Сохранить", self.save_graph, StandardKey.Save)
        self._make_action(file_menu, "Сохранить как...", self.save_graph_as, StandardKey.SaveAs)

        file_menu.addSeparator()
        self._make_action(
            file_menu,
            "Закрыть вкладку",
            lambda: self._close_tab(self.tabs.currentIndex()),
            StandardKey.Close,
        )

        edit_menu = self.menuBar().addMenu("Правка")
        self._make_action(edit_menu, "Отменить", self._undo, StandardKey.Undo)
        self._make_action(edit_menu, "Повторить", self._redo, StandardKey.Redo)

        self.nodes_menu = self.menuBar().addMenu("Добавить узел")
        self._populate_nodes_menu()

        view_menu = self.menuBar().addMenu("Вид")
        for dock in self._docks:
            view_menu.addAction(dock.toggleViewAction())

        self.examples_menu = self.menuBar().addMenu("Примеры")
        self.examples_menu.aboutToShow.connect(self._refresh_examples_menu)
        self._refresh_examples_menu()

        settings_menu = self.menuBar().addMenu("Настройки")
        settings_menu.addAction("Путь к Tesseract OCR...", self.choose_tesseract_path)
        settings_menu.addAction("Хоткей аварийной остановки...", self.choose_stop_hotkey)
        settings_menu.addAction("Настройки ИИ (для ИИ-шаблонов)...", self.choose_ai_settings)

    def _populate_nodes_menu(self):
        self.nodes_menu.clear()
        self.nodes_menu.addAction("ИИ-ассистент: собрать граф...", self.show_ai_assistant)
        self.nodes_menu.addAction("Обновить список нод (папка user_nodes)", self._refresh_user_nodes)
        self.nodes_menu.addSeparator()
        for node_cls in ALL_NODES:
            node_type = f"{node_cls.__identifier__}.{node_cls.__name__}"
            label = f"{CATEGORY_LABELS.get(node_cls.__identifier__, node_cls.__identifier__)} — {node_cls.NODE_NAME}"
            self.nodes_menu.addAction(label, lambda checked=False, nt=node_type: self._add_node(nt))

    def _refresh_user_nodes(self):
        """Пересканирует user_nodes/ и подключает новые классы нод везде, где
        нужно: в общий список, в уже открытые вкладки (свежие вкладки и так
        получат их через ALL_NODES) и в панель/меню нод — без перезапуска."""
        from core.node_loader import discover_user_nodes

        discovered = discover_user_nodes(log=lambda msg: self.log_view.appendPlainText(f"[Ноды] {msg}"))
        existing_types = {f"{cls.__identifier__}.{cls.__name__}" for cls in ALL_NODES}
        new_classes = [
            cls for cls in discovered if f"{cls.__identifier__}.{cls.__name__}" not in existing_types
        ]
        if not new_classes:
            self.status_label.setText("Новых нод не найдено")
            return

        ALL_NODES.extend(new_classes)

        for tab in self._tab_data.values():
            try:
                tab.graph.register_nodes(new_classes)
            except Exception as exc:
                self.log_view.appendPlainText(f"[Ноды] Не удалось добавить в открытую вкладку: {exc}")

        if self.nodes_tree is not None:
            self.nodes_tree.update()
            self._remove_tree_category(self.nodes_tree, "nodeGraphQt.nodes")

        self._populate_nodes_menu()
        self.status_label.setText(f"Добавлено новых нод: {len(new_classes)}")

    def _refresh_examples_menu(self):
        """Перечитывает папку examples/ при каждом открытии меню, чтобы новые
        файлы появлялись без перезапуска приложения."""
        self.examples_menu.clear()
        if not os.path.isdir(EXAMPLES_DIR):
            action = self.examples_menu.addAction("Папка examples не найдена")
            action.setEnabled(False)
            return
        files = sorted(f for f in os.listdir(EXAMPLES_DIR) if f.lower().endswith(".json"))
        if not files:
            action = self.examples_menu.addAction("Нет примеров")
            action.setEnabled(False)
            return
        for filename in files:
            path = os.path.join(EXAMPLES_DIR, filename)
            title = os.path.splitext(filename)[0]
            self.examples_menu.addAction(title, lambda checked=False, p=path: self.open_graph(p))

    def _delete_selected_nodes(self, graph):
        nodes = graph.selected_nodes()
        if nodes:
            graph.delete_nodes(nodes)

    # ---- точки останова ----
    def _toggle_breakpoints(self, graph):
        nodes = [n for n in graph.selected_nodes() if isinstance(n, ExecNode)]
        if not nodes:
            return
        state = not all(n.get_property(BREAKPOINT_PROP) for n in nodes)
        for node in nodes:
            node.set_property(BREAKPOINT_PROP, state)

    def _on_node_property_changed(self, node, name, value):
        if name == BREAKPOINT_PROP:
            self._apply_breakpoint_marker(node)

    @staticmethod
    def _apply_breakpoint_marker(node):
        """Красная рамка вокруг ноды с точкой останова. Рамка — это чисто
        вид (сама точка — свойство ноды), поэтому без записи в undo."""
        color = BREAKPOINT_BORDER_COLOR if node.get_property(BREAKPOINT_PROP) else DEFAULT_BORDER_COLOR
        node.set_property("border_color", color, push_undo=False)

    # ---- вкладки ----
    def _create_graph(self):
        graph = NodeGraph()
        graph.set_acyclic(False)
        graph.register_nodes(ALL_NODES)
        graph_menu = graph.get_context_menu("graph")
        if graph_menu is not None:
            graph_menu.add_command("Удалить", self._delete_selected_nodes, shortcut="Del")
            graph_menu.add_command("Точка останова вкл/выкл", self._toggle_breakpoints, shortcut="F8")
        graph.property_changed.connect(self._on_node_property_changed)
        return graph

    def _add_graph_tab(self, title, seed_default=True):
        graph = self._create_graph()
        self._ensure_nodes_tree(graph)
        if seed_default:
            graph.create_node("automation.events.StartNode", name="Старт", pos=[0, 0])

        graph.undo_stack().clear()  # стартовая нода «Старт» — не несохранённое изменение

        properties_bin = PropertiesBinWidget(node_graph=graph)
        self.properties_stack.addWidget(properties_bin)

        tab = GraphTab(graph, properties_bin, title)
        self._tab_data[graph.widget] = tab
        graph.undo_stack().cleanChanged.connect(lambda _clean, t=tab: self._on_clean_changed(t))

        index = self.tabs.addTab(graph.widget, title)
        self.tabs.setCurrentIndex(index)
        return tab

    def _current_tab(self):
        widget = self.tabs.currentWidget()
        return self._tab_data.get(widget)

    def _update_tab_title(self, tab):
        if tab.session_path:
            tab.title = os.path.splitext(os.path.basename(tab.session_path))[0]
        index = self.tabs.indexOf(tab.graph.widget)
        if index != -1:
            self.tabs.setTabText(index, tab.title + (" *" if tab.is_modified() else ""))

    def _on_clean_changed(self, tab):
        # QUndoStack шлёт cleanChanged и из своего деструктора — при закрытии
        # вкладки/приложения виджеты к этому моменту уже могут быть удалены.
        try:
            self._update_tab_title(tab)
        except RuntimeError:
            pass

    def _confirm_discard(self, tab):
        """Спрашивает про несохранённые изменения. False — пользователь
        передумал закрывать (или сохранение не удалось/отменено)."""
        if not tab.is_modified():
            return True
        self.tabs.setCurrentWidget(tab.graph.widget)
        buttons = QtWidgets.QMessageBox.StandardButton
        answer = QtWidgets.QMessageBox.question(
            self,
            "Несохранённые изменения",
            f"Сохранить изменения в «{tab.title}»?",
            buttons.Save | buttons.Discard | buttons.Cancel,
            buttons.Save,
        )
        if answer == buttons.Save:
            return self._save_tab(tab)
        return answer == buttons.Discard

    def _close_tab(self, index):
        widget = self.tabs.widget(index)
        tab = self._tab_data.get(widget)
        if tab is None or not self._confirm_discard(tab):
            return
        self._tab_data.pop(widget, None)
        if tab.engine and tab.engine.isRunning():
            tab.engine.stop()
            tab.engine.wait(2000)
        self.properties_stack.removeWidget(tab.properties_bin)
        tab.properties_bin.deleteLater()
        self.tabs.removeTab(index)
        widget.deleteLater()
        if self.tabs.count() == 0:
            self.new_graph()

    def _on_tab_changed(self, index):
        tab = self._current_tab()
        if tab is None:
            return
        self.properties_stack.setCurrentWidget(tab.properties_bin)
        self._sync_run_actions()
        if not tab.is_running():
            self.status_label.setText("Готово")
        elif tab.engine.is_paused():
            self.status_label.setText("Пауза")
        else:
            self.status_label.setText("Выполняется...")

    def _step_tab(self, delta):
        count = self.tabs.count()
        if count:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + delta) % count)

    def _undo(self):
        tab = self._current_tab()
        if tab:
            tab.graph.undo_stack().undo()

    def _redo(self):
        tab = self._current_tab()
        if tab:
            tab.graph.undo_stack().redo()

    def _add_node(self, node_type):
        tab = self._current_tab()
        if tab is None:
            return
        pos = list(self._next_node_pos)
        self._next_node_pos[1] += 120
        tab.graph.create_node(node_type, pos=pos)

    # ---- операции с файлом графа ----
    def new_graph(self):
        self._new_tab_counter += 1
        title = "Без имени" if self._new_tab_counter == 1 else f"Без имени {self._new_tab_counter}"
        self._add_graph_tab(title)

    def open_graph(self, path=None):
        if not path:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Открыть граф", "", "Graph (*.json)")
        if not path:
            return
        title = os.path.splitext(os.path.basename(path))[0]
        tab = self._add_graph_tab(title, seed_default=False)
        tab.graph.load_session(path)
        tab.session_path = path
        for node in tab.graph.all_nodes():
            if isinstance(node, ExecNode):
                self._apply_breakpoint_marker(node)
        tab.graph.undo_stack().clear()
        self._update_tab_title(tab)

    def save_graph(self):
        tab = self._current_tab()
        if tab is not None:
            self._save_tab(tab)

    def save_graph_as(self):
        tab = self._current_tab()
        if tab is not None:
            self._save_tab(tab, ask_path=True)

    def _save_tab(self, tab, ask_path=False):
        """Returns: True, если граф действительно сохранён."""
        path = tab.session_path
        if ask_path or not path:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Сохранить граф", tab.session_path or f"{tab.title}.json", "Graph (*.json)"
            )
            if not path:
                return False
        try:
            tab.graph.save_session(path)
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "Сохранение", f"Не удалось сохранить {path}:\n{exc}")
            return False
        tab.session_path = path
        tab.graph.undo_stack().setClean()
        self._update_tab_title(tab)
        self.status_label.setText(f"Сохранено: {path}")
        return True

    def choose_tesseract_path(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Путь к tesseract.exe",
            get_tesseract_path() or "C:/Program Files/Tesseract-OCR",
            "tesseract.exe (tesseract.exe);;Все файлы (*.*)",
        )
        if path:
            set_tesseract_path(path)
            self.status_label.setText("Путь к Tesseract OCR обновлён")

    def show_ai_assistant(self):
        self.ai_dock.show()
        self.ai_dock.raise_()
        self.ai_chat.setFocus()

    def _tab_is_open(self, tab):
        return tab is not None and tab in self._tab_data.values()

    def _build_ai_graph(self, plan, catalog, template_paths):
        """Строит план из чата ИИ. Повторное «Построить граф» в том же чате
        заменяет граф в его вкладке (одним шагом отмены), а не плодит вкладки."""
        tab = self._ai_tab if self._tab_is_open(self._ai_tab) else None
        if tab is None:
            tab = self._ai_tab = self._add_graph_tab("ИИ: граф", seed_default=False)
        else:
            self.tabs.setCurrentWidget(tab.graph.widget)
        graph = tab.graph
        graph.begin_undo("ИИ: построить граф")
        try:
            graph.delete_nodes(graph.all_nodes())
            apply_plan(graph, plan, catalog, template_paths)
        finally:
            graph.end_undo()
        graph.fit_to_selection()  # ничего не выделено — охватит весь граф
        self.log_view.appendPlainText(f"[ИИ] Граф построен: {plan.get('summary', '')}")
        self.status_label.setText("Граф от ИИ построен — проверь его перед запуском")
        return tab.title

    def choose_ai_settings(self):
        if edit_ai_settings(self):
            self.status_label.setText("Настройки ИИ сохранены")

    def choose_stop_hotkey(self):
        current = get_stop_hotkey()
        text, ok = QtWidgets.QInputDialog.getText(
            self,
            "Хоткей аварийной остановки",
            "Комбинация клавиш (например: f9, ctrl+alt+q):",
            text=current,
        )
        if not ok or not text.strip():
            return
        self._apply_stop_hotkey(text.strip().lower(), persist=True)

    def _apply_stop_hotkey(self, hotkey, persist=False):
        try:
            self._hotkey_manager.apply(hotkey)
        except RuntimeError as exc:
            QtWidgets.QMessageBox.warning(self, "Хоткей", str(exc))
            self.log_view.appendPlainText(f"[Хоткей] {exc}")
            return
        if persist:
            set_stop_hotkey(hotkey)
        self.status_label.setText(f"Хоткей аварийной остановки: {hotkey.upper()}")

    def _stop_all_engines(self):
        """Аварийная остановка по глобальному хоткею — глушит все запущенные
        вкладки разом, а не только активную: с фокусом не на редакторе
        пользователь не может выбрать «нужную» вкладку."""
        running_tabs = [tab for tab in self._tab_data.values() if tab.engine and tab.engine.isRunning()]
        for tab in running_tabs:
            tab.engine.stop()
        if not running_tabs:
            return
        self.log_view.appendPlainText(f"--- Аварийная остановка (хоткей) — вкладок: {len(running_tabs)} ---")
        self.status_label.setText("Остановка (хоткей)...")
        self._sync_run_actions()

    # ---- выполнение ----
    def _sync_run_actions(self):
        tab = self._current_tab() if self._tab_data else None
        running = bool(tab and tab.is_running())
        paused = running and tab.engine.is_paused()
        self.run_action.setText("▶ Продолжить" if paused else "▶ Запустить")
        self.run_action.setEnabled(tab is not None and (not running or paused))
        self.pause_action.setEnabled(running and not paused)
        self.step_action.setEnabled(tab is not None and (not running or paused))
        self.stop_action.setEnabled(running)

    def start_run(self, start_paused=False):
        tab = self._current_tab()
        if tab is None:
            return
        if tab.is_running():
            if tab.engine.is_paused():
                tab.engine.resume()
                self.status_label.setText("Выполняется...")
                self._sync_run_actions()
            return
        suffix = " по шагам" if start_paused else ""
        self.log_view.appendPlainText(f"--- [{tab.title}] Запуск{suffix} ---")

        tab.engine = GraphEngine(tab.graph, start_paused=start_paused)
        tab.engine.node_started.connect(lambda node_id, t=tab: self._on_node_started(t, node_id))
        tab.engine.node_finished.connect(lambda node_id, t=tab: self._on_node_finished(t, node_id))
        tab.engine.log_message.connect(lambda message, t=tab: self._on_log_message(t, message))
        tab.engine.paused.connect(lambda node_id, t=tab: self._on_paused(t, node_id))
        tab.engine.resumed.connect(lambda t=tab: self._on_resumed(t))
        tab.engine.run_finished.connect(lambda t=tab: self._on_run_finished(t))

        self.status_label.setText("Выполняется...")
        tab.engine.start()
        self._sync_run_actions()

    def pause_run(self):
        tab = self._current_tab()
        if tab and tab.is_running():
            tab.engine.pause()
            self.status_label.setText("Пауза перед следующей нодой...")
            self._sync_run_actions()

    def step_run(self):
        tab = self._current_tab()
        if tab is None:
            return
        if not tab.is_running():
            self.start_run(start_paused=True)
            return
        tab.engine.step()
        self.status_label.setText("Шаг...")

    def stop_run(self):
        tab = self._current_tab()
        if tab and tab.engine:
            tab.engine.stop()
            self.status_label.setText("Остановка...")

    @staticmethod
    def _set_color_silently(node, color):
        """Подсветка выполняемой ноды — не правка графа: ``node.set_color``
        пишет в undo-стек, и каждый прогон засорял бы «Отменить» и помечал
        граф как изменённый."""
        node.set_property("color", tuple(color[:3]) + (255,), push_undo=False)

    def _on_node_started(self, tab, node_id):
        node = tab.graph.get_node_by_id(node_id)
        if node is None:
            return
        # setdefault: при параллельных цепочках одна нода может «стартовать»
        # повторно, пока ещё подсвечена, — исходный цвет не должен затереться жёлтым.
        tab.node_colors.setdefault(node_id, node.color())
        self._set_color_silently(node, RUNNING_COLOR)

    def _on_node_finished(self, tab, node_id):
        node = tab.graph.get_node_by_id(node_id)
        original = tab.node_colors.pop(node_id, None)
        if node is not None and original is not None:
            self._set_color_silently(node, original)

    def _on_paused(self, tab, node_id):
        node = tab.graph.get_node_by_id(node_id)
        name = node.name() if node is not None else node_id
        self.log_view.appendPlainText(f"[{tab.title}] Пауза перед «{name}»")
        if self._current_tab() is tab:
            self.status_label.setText(f"Пауза: «{name}» (▶ — продолжить, ⏭ — шаг)")
            self._sync_run_actions()

    def _on_resumed(self, tab):
        if self._current_tab() is tab:
            self.status_label.setText("Выполняется...")
            self._sync_run_actions()

    def _on_log_message(self, tab, message):
        self.log_view.appendPlainText(f"[{tab.title}] {message}")

    def _on_run_finished(self, tab):
        for node_id in list(tab.node_colors):  # на случай остановки посреди шага
            self._on_node_finished(tab, node_id)
        if self._current_tab() is tab:
            self.status_label.setText("Готово")
            self._sync_run_actions()

    def closeEvent(self, event):
        for tab in list(self._tab_data.values()):
            if not self._confirm_discard(tab):
                event.ignore()
                return
        self._hotkey_manager.unregister()
        for index in range(self.tabs.count()):
            tab = self._tab_data.get(self.tabs.widget(index))
            if tab and tab.engine and tab.engine.isRunning():
                tab.engine.stop()
                tab.engine.wait(2000)
        super().closeEvent(event)
