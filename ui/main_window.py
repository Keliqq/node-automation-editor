from NodeGraphQt import NodeGraph, NodesTreeWidget, PropertiesBinWidget
from PySide6 import QtCore, QtWidgets

import core.nodegraphqt_patches  # noqa: F401  чинит числовые виджеты NodeGraphQt до их использования
from core.engine import GraphEngine
from core.ocr import get_tesseract_path, set_tesseract_path
from nodes import ALL_NODES

RUNNING_COLOR = (240, 200, 60)

CATEGORY_LABELS = {
    "automation.events": "События",
    "automation.flow": "Логика",
    "automation.vision": "Зрение",
    "automation.actions": "Действия",
    "automation.debug": "Отладка",
}


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Редактор нод для автоматизации игр")
        self.resize(1400, 900)

        self.graph = NodeGraph()
        self.graph.register_nodes(ALL_NODES)

        self._node_colors = {}
        self.engine = None
        self._session_path = None
        self._next_node_pos = [300, 0]

        self._build_ui()
        self._build_menu()
        self._seed_default_graph()

    def _build_ui(self):
        self.setCentralWidget(self.graph.widget)

        self.nodes_tree = NodesTreeWidget(node_graph=self.graph)
        for category, label in CATEGORY_LABELS.items():
            self.nodes_tree.set_category_label(category, label)
        self._remove_tree_category("nodeGraphQt.nodes")  # встроенная нода Backdrop, здесь не нужна
        palette_dock = QtWidgets.QDockWidget("Ноды (перетащи на холст)", self)
        palette_dock.setObjectName("palette_dock")
        palette_dock.setWidget(self.nodes_tree)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, palette_dock)

        self.properties_bin = PropertiesBinWidget(node_graph=self.graph)
        properties_dock = QtWidgets.QDockWidget("Свойства", self)
        properties_dock.setObjectName("properties_dock")
        properties_dock.setWidget(self.properties_bin)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, properties_dock)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        log_dock = QtWidgets.QDockWidget("Лог", self)
        log_dock.setObjectName("log_dock")
        log_dock.setWidget(self.log_view)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, log_dock)

        self._docks = [palette_dock, properties_dock, log_dock]

        toolbar = QtWidgets.QToolBar("Выполнение")
        self.addToolBar(toolbar)

        self.run_action = toolbar.addAction("▶ Запустить")
        self.run_action.triggered.connect(self.start_run)

        self.stop_action = toolbar.addAction("⏹ Остановить")
        self.stop_action.triggered.connect(self.stop_run)
        self.stop_action.setEnabled(False)

        self.status_label = QtWidgets.QLabel("Готово")
        self.statusBar().addPermanentWidget(self.status_label)

    def _remove_tree_category(self, category_label):
        tree = self.nodes_tree
        for i in reversed(range(tree.topLevelItemCount())):
            item = tree.topLevelItem(i)
            if item is not None and item.text(0) == category_label:
                tree.takeTopLevelItem(i)

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("Файл")
        file_menu.addAction("Новый граф", self.new_graph)
        file_menu.addAction("Открыть...", self.open_graph)
        file_menu.addAction("Сохранить", self.save_graph)
        file_menu.addAction("Сохранить как...", self.save_graph_as)

        edit_menu = self.menuBar().addMenu("Правка")
        edit_menu.addAction("Отменить", self.graph.undo_stack().undo)
        edit_menu.addAction("Повторить", self.graph.undo_stack().redo)

        nodes_menu = self.menuBar().addMenu("Добавить узел")
        for node_cls in ALL_NODES:
            node_type = f"{node_cls.__identifier__}.{node_cls.__name__}"
            label = f"{CATEGORY_LABELS.get(node_cls.__identifier__, node_cls.__identifier__)} — {node_cls.NODE_NAME}"
            nodes_menu.addAction(label, lambda checked=False, nt=node_type: self._add_node(nt))

        view_menu = self.menuBar().addMenu("Вид")
        for dock in self._docks:
            view_menu.addAction(dock.toggleViewAction())

        settings_menu = self.menuBar().addMenu("Настройки")
        settings_menu.addAction("Путь к Tesseract OCR...", self.choose_tesseract_path)

    def _seed_default_graph(self):
        self.graph.create_node("automation.events.StartNode", name="Старт", pos=[0, 0])

    def _add_node(self, node_type):
        pos = list(self._next_node_pos)
        self._next_node_pos[1] += 120
        self.graph.create_node(node_type, pos=pos)

    # ---- операции с файлом графа ----
    def new_graph(self):
        self.graph.clear_session()
        self._session_path = None
        self._seed_default_graph()

    def open_graph(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Открыть граф", "", "Graph (*.json)")
        if path:
            self.graph.load_session(path)
            self._session_path = path

    def save_graph(self):
        if not self._session_path:
            return self.save_graph_as()
        self.graph.save_session(self._session_path)

    def save_graph_as(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Сохранить граф", "graph.json", "Graph (*.json)")
        if path:
            self.graph.save_session(path)
            self._session_path = path

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

    # ---- выполнение ----
    def start_run(self):
        if self.engine and self.engine.isRunning():
            return
        self.log_view.clear()
        self.engine = GraphEngine(self.graph)
        self.engine.node_started.connect(self._on_node_started)
        self.engine.node_finished.connect(self._on_node_finished)
        self.engine.log_message.connect(self._on_log_message)
        self.engine.run_finished.connect(self._on_run_finished)

        self.run_action.setEnabled(False)
        self.stop_action.setEnabled(True)
        self.status_label.setText("Выполняется...")
        self.engine.start()

    def stop_run(self):
        if self.engine:
            self.engine.stop()
            self.status_label.setText("Остановка...")

    def _on_node_started(self, node_id):
        node = self.graph.get_node_by_id(node_id)
        if node is None:
            return
        self._node_colors[node_id] = node.color()
        node.set_color(*RUNNING_COLOR)

    def _on_node_finished(self, node_id):
        node = self.graph.get_node_by_id(node_id)
        if node is None:
            return
        original = self._node_colors.pop(node_id, None)
        if original is not None:
            node.set_color(*original)

    def _on_log_message(self, message):
        self.log_view.appendPlainText(message)

    def _on_run_finished(self):
        self.run_action.setEnabled(True)
        self.stop_action.setEnabled(False)
        self.status_label.setText("Готово")

    def closeEvent(self, event):
        if self.engine and self.engine.isRunning():
            self.engine.stop()
            self.engine.wait(2000)
        super().closeEvent(event)
