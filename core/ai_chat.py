"""Окно «ИИ-ассистент»: чат с моделью, которая собирает граф.

Показывает ответ по мере генерации и отдельно — ход мыслей модели (у
Claude — краткое изложение рассуждений, у Qwen/DeepSeek — их <think>),
чтобы было видно, что она не зависла. План графа из ответа проверяется и
показывается карточкой с кнопкой «Построить граф»; ошибки плана сначала
сами уходят модели на исправление.

Сетевой запрос идёт в обычном потоке, куски ответа попадают в окно через
сигналы (queued-соединение в GUI-поток).
"""

import threading

import cv2
from PySide6 import QtCore, QtGui, QtWidgets

from core.ai_graph import TEMPLATE_PROP, GraphChat, build_catalog
from core.ai_template_dialog import edit_ai_settings, grab_screen_hidden, template_from_screen
from core.ai_vision import ANTHROPIC_MODEL, PROVIDER_OPENAI, AiVisionError, get_ai_settings
from core.screen import MONITOR_ALL, list_monitors, monitor_label

# Сколько раз подряд ошибки плана уходят модели без участия пользователя.
MAX_AUTO_FIXES = 2

_FLUSH_MS = 80  # перерисовка текста не чаще, чем раз в столько мс
_PLAN_FENCE = "```"


class _Relay(QtCore.QObject):
    thinking = QtCore.Signal(str)
    text = QtCore.Signal(str)
    finished = QtCore.Signal(object)
    failed = QtCore.Signal(str)


def _bubble(object_name):
    frame = QtWidgets.QFrame()
    frame.setObjectName(object_name)
    layout = QtWidgets.QVBoxLayout(frame)
    layout.setContentsMargins(10, 8, 10, 8)
    layout.setSpacing(4)
    return frame, layout


def _text_label(markdown=False):
    label = QtWidgets.QLabel()
    label.setWordWrap(True)
    label.setTextFormat(QtCore.Qt.TextFormat.MarkdownText if markdown else QtCore.Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(
        QtCore.Qt.TextInteractionFlag.TextSelectableByMouse | QtCore.Qt.TextInteractionFlag.LinksAccessibleByMouse
    )
    label.setOpenExternalLinks(True)
    return label


class _AssistantMessage(QtWidgets.QWidget):
    """Ответ модели: сворачиваемый ход мыслей + текст, растущие по мере
    прихода кусков."""

    def __init__(self, on_update, parent=None):
        super().__init__(parent)
        self._on_update = on_update
        frame, layout = _bubble("assistantBubble")
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 40, 0)
        outer.addWidget(frame)

        self._thinking_toggle = QtWidgets.QToolButton()
        self._thinking_toggle.setCheckable(True)
        self._thinking_toggle.setChecked(True)
        self._thinking_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._thinking_toggle.setArrowType(QtCore.Qt.ArrowType.DownArrow)
        self._thinking_toggle.setAutoRaise(True)
        self._thinking_toggle.setText("Думает…")
        self._thinking_toggle.toggled.connect(self._on_toggle)
        self._thinking_label = _text_label()
        self._thinking_label.setObjectName("thinkingText")
        self._thinking_toggle.hide()
        self._thinking_label.hide()

        self._text_label = _text_label(markdown=True)
        self._status = QtWidgets.QLabel("⏳ ждём ответ модели…")
        self._status.setObjectName("statusText")

        layout.addWidget(self._thinking_toggle, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._thinking_label)
        layout.addWidget(self._text_label)
        layout.addWidget(self._status)

        self._thinking = []
        self._text = []
        self._dirty = False
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(_FLUSH_MS)
        self._timer.timeout.connect(self._flush)
        self._timer.start()

    def _on_toggle(self, checked):
        self._thinking_label.setVisible(checked)
        self._thinking_toggle.setArrowType(QtCore.Qt.ArrowType.DownArrow if checked else QtCore.Qt.ArrowType.RightArrow)

    def append_thinking(self, piece):
        self._thinking.append(piece)
        self._dirty = True

    def append_text(self, piece):
        if not self._text and self._thinking_toggle.isChecked():
            # Пошёл ответ — рассуждения сворачиваем, их можно раскрыть.
            self._thinking_toggle.setChecked(False)
        self._text.append(piece)
        self._dirty = True

    def _flush(self):
        if not self._dirty:
            return
        self._dirty = False
        if self._thinking:
            self._thinking_toggle.show()
            self._thinking_label.setVisible(self._thinking_toggle.isChecked())
            self._thinking_label.setText("".join(self._thinking).strip())
        text = "".join(self._text)
        fence = text.find(_PLAN_FENCE)
        if fence >= 0:
            # JSON плана по ходу генерации не показываем — будет карточка.
            text = text[:fence]
            self._status.setText("⏳ пишет план графа…")
        elif self._text:
            self._status.setText("⏳ пишет ответ…")
        elif self._thinking:
            self._status.setText("⏳ думает…")
        self._text_label.setText(text.strip())
        self._on_update()

    def finish(self, visible_text):
        self._timer.stop()
        self._dirty = True
        self._flush()
        self._text_label.setText(visible_text or "")
        self._text_label.setVisible(bool(visible_text))
        self._status.hide()
        if self._thinking:
            self._thinking_toggle.setText(f"Ход мыслей ({len(''.join(self._thinking))} симв.)")

    def fail(self, message):
        self.finish("".join(self._text).split(_PLAN_FENCE)[0].strip())
        self._status.setText(f"⚠ {message}")
        self._status.setStyleSheet("color: #d9534f;")
        self._status.show()


class _PlanCard(QtWidgets.QFrame):
    def __init__(self, plan, errors, on_build, parent=None):
        super().__init__(parent)
        self.setObjectName("planCard")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)

        title = QtWidgets.QLabel("📋 План графа")
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        names = [str(spec.get("type")) for spec in plan.get("nodes") or [] if isinstance(spec, dict)]
        counts = {name: names.count(name) for name in dict.fromkeys(names)}
        lines = [plan.get("summary") or ""]
        lines.append("Ноды: " + ", ".join(f"{name} ×{n}" if n > 1 else name for name, n in counts.items()))
        templates = [spec["template"] for spec in plan.get("nodes") or [] if isinstance(spec, dict) and spec.get("template")]
        if templates:
            lines.append("Шаблоны: " + "; ".join(templates))
        if plan.get("notes"):
            lines.append("Заметки: " + str(plan["notes"]))
        body = _text_label()
        body.setText("\n".join(line for line in lines if line))
        layout.addWidget(body)

        if errors:
            error_label = _text_label()
            error_label.setObjectName("errorText")
            error_label.setText("Ошибки в плане:\n- " + "\n- ".join(errors))
            layout.addWidget(error_label)
        else:
            self.build_button = QtWidgets.QPushButton("Построить граф")
            self.build_button.clicked.connect(lambda: on_build(plan))
            layout.addWidget(self.build_button, 0, QtCore.Qt.AlignmentFlag.AlignLeft)


class AiChatWidget(QtWidgets.QWidget):
    """Чат с ИИ о графе.

    Args:
        node_classes: функция, возвращающая текущий список классов нод
            (``ALL_NODES`` может пополниться после «Обновить список нод»).
        builder: ``builder(plan, catalog, template_paths) -> str`` — строит
            граф в редакторе и возвращает название вкладки.
    """

    def __init__(self, node_classes, builder, parent=None):
        super().__init__(parent)
        self._node_classes = node_classes
        self._builder = builder
        self._chat = None
        self._busy = False
        self._stop = threading.Event()
        self._relay = None
        self._current = None
        self._generation = 0  # ответы запросов из прошлого «Нового чата» игнорируются
        self._auto_fixes = 0
        self._last_screen = None
        self._last_sent_text = ""
        self._template_cache = {}  # описание -> путь, чтобы при перестройке не резать шаблоны заново

        self._provider_label = QtWidgets.QLabel()
        self._provider_label.setObjectName("statusText")
        new_chat = QtWidgets.QToolButton()
        new_chat.setText("Новый чат")
        new_chat.clicked.connect(self.new_chat)
        settings = QtWidgets.QToolButton()
        settings.setText("⚙")
        settings.setToolTip("Настройки ИИ")
        settings.clicked.connect(self._open_settings)
        header = QtWidgets.QHBoxLayout()
        header.addWidget(self._provider_label, 1)
        header.addWidget(new_chat)
        header.addWidget(settings)

        self._messages = QtWidgets.QVBoxLayout()
        self._messages.setSpacing(8)
        self._messages.addStretch(1)
        container = QtWidgets.QWidget()
        container.setLayout(self._messages)
        self._scroll = QtWidgets.QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(container)
        self._scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._input = QtWidgets.QPlainTextEdit()
        self._input.setPlaceholderText(
            "Опиши, что должна делать программа. Например: продавай героям у прилавка то, что они просят.\n"
            "Ctrl+Enter — отправить."
        )
        self._input.setMaximumHeight(90)
        send_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Return"), self._input)
        send_shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetShortcut)
        send_shortcut.activated.connect(self._on_send_clicked)

        self._attach = QtWidgets.QCheckBox("📷 Скриншот")
        self._attach.setToolTip("Приложить к сообщению скриншот — редактор на секунду скроется")
        self._attach.setChecked(True)
        self._monitor = QtWidgets.QComboBox()
        self._monitor.addItem("Все мониторы", MONITOR_ALL)
        for monitor in list_monitors():
            self._monitor.addItem(monitor_label(monitor), monitor["index"])
        self._send = QtWidgets.QPushButton("Отправить")
        self._send.clicked.connect(self._on_send_clicked)
        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(self._attach)
        controls.addWidget(self._monitor, 1)
        controls.addWidget(self._send)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self._scroll, 1)
        layout.addWidget(self._input)
        layout.addLayout(controls)

        self.setStyleSheet(
            "#userBubble { background: rgba(80, 140, 255, 45); border-radius: 8px; }"
            "#assistantBubble { background: rgba(128, 128, 128, 35); border-radius: 8px; }"
            "#planCard { border: 1px solid rgba(80, 180, 120, 160); border-radius: 8px; }"
            "#thinkingText, #statusText, #noteText { color: gray; }"
            "#thinkingText { font-style: italic; }"
            "#errorText { color: #d9534f; }"
        )
        self._update_provider_label()
        self._add_note(
            "Опиши задачу — ИИ задаст вопросы, если что-то неясно, и предложит граф. "
            "Перед отправкой открой игру/программу в нужном состоянии: с первым сообщением уйдёт скриншот."
        )

    # ---- служебное ----
    def _update_provider_label(self):
        settings = get_ai_settings()
        if settings["provider"] == PROVIDER_OPENAI:
            model = settings["openai_model"] or "модель не задана"
            self._provider_label.setText(f"Модель: {model} (OpenAI-совместимый)")
        else:
            self._provider_label.setText(f"Модель: Claude ({ANTHROPIC_MODEL})")

    def _open_settings(self):
        if edit_ai_settings(self):
            self._update_provider_label()

    def showEvent(self, event):
        self._update_provider_label()
        super().showEvent(event)

    def _add_widget(self, widget):
        self._messages.insertWidget(self._messages.count() - 1, widget)
        self.scroll_to_bottom_soon()

    def _add_note(self, text, error=False):
        label = _text_label()
        label.setObjectName("errorText" if error else "noteText")
        label.setText(text)
        self._add_widget(label)

    def scroll_to_bottom_soon(self):
        bar = self._scroll.verticalScrollBar()
        # Прокручиваем, только если пользователь и так был внизу — не
        # выдёргиваем его, если он читает что-то выше.
        if bar.maximum() - bar.value() < 80:
            QtCore.QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    def _set_busy(self, busy):
        self._busy = busy
        self._send.setText("Стоп" if busy else "Отправить")
        self._input.setReadOnly(busy)

    # ---- разговор ----
    def new_chat(self):
        if self._busy:
            self._stop.set()
            self._set_busy(False)
        self._generation += 1
        self._chat = None
        self._auto_fixes = 0
        self._last_screen = None
        while self._messages.count() > 1:
            item = self._messages.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._attach.setChecked(True)
        self._add_note("Новый чат.")

    def _on_send_clicked(self):
        if self._busy:
            self._stop.set()
            return
        text = self._input.toPlainText().strip()
        if not text:
            return
        self._input.clear()
        self._auto_fixes = 0
        self._send_message(text, from_user=True)

    def _send_message(self, text, from_user):
        if self._chat is None:
            self._chat = GraphChat(build_catalog(self._node_classes()))

        screen = None
        if from_user and self._attach.isChecked():
            screen = grab_screen_hidden(self._monitor.currentData())
            self._last_screen = screen
            self._attach.setChecked(False)  # дальше — только когда попросят

        if from_user:
            self._add_user_bubble(text, screen)
        self._last_sent_text = text if from_user else ""

        message = _AssistantMessage(self.scroll_to_bottom_soon)
        self._add_widget(message)
        self._current = message

        relay = _Relay()
        relay.thinking.connect(message.append_thinking)
        relay.text.connect(message.append_text)
        generation = self._generation
        relay.finished.connect(lambda result: generation == self._generation and self._on_finished(result))
        relay.failed.connect(lambda error: generation == self._generation and self._on_failed(error))
        self._relay = relay  # держим ссылку, пока поток работает
        self._stop = stop = threading.Event()  # своё событие у каждого запроса
        self._set_busy(True)

        chat = self._chat

        def work():
            try:
                result = chat.send(text, screen, relay.thinking.emit, relay.text.emit, stop.is_set)
            except AiVisionError as exc:
                relay.failed.emit(str(exc))
            except Exception as exc:  # noqa: BLE001 — показываем в чате, а не роняем поток молча
                relay.failed.emit(f"{type(exc).__name__}: {exc}")
            else:
                relay.finished.emit(result)

        threading.Thread(target=work, daemon=True).start()

    def _add_user_bubble(self, text, screen):
        frame, layout = _bubble("userBubble")
        label = _text_label()
        label.setText(text)
        layout.addWidget(label)
        if screen is not None:
            thumb = QtWidgets.QLabel()
            rgb = cv2.cvtColor(screen, cv2.COLOR_BGR2RGB)
            h, w = rgb.shape[:2]
            image = QtGui.QImage(rgb.data, w, h, w * 3, QtGui.QImage.Format.Format_RGB888).copy()
            thumb.setPixmap(
                QtGui.QPixmap.fromImage(image).scaledToWidth(220, QtCore.Qt.TransformationMode.SmoothTransformation)
            )
            thumb.setToolTip("Скриншот, приложенный к сообщению")
            layout.addWidget(thumb)
        row = QtWidgets.QWidget()
        outer = QtWidgets.QHBoxLayout(row)
        outer.setContentsMargins(40, 0, 0, 0)
        outer.addWidget(frame)
        self._add_widget(row)

    def _on_finished(self, result):
        self._set_busy(False)
        self._current.finish(result["text"])
        plan, errors = result["plan"], result["errors"]
        if result["stopped"] or plan is None:
            return
        if errors and self._auto_fixes < MAX_AUTO_FIXES:
            self._auto_fixes += 1
            self._add_note(
                f"Проверка нашла в плане ошибки ({len(errors)}) — прошу ИИ исправить "
                f"(попытка {self._auto_fixes} из {MAX_AUTO_FIXES}):\n- " + "\n- ".join(errors)
            )
            self._send_message(GraphChat.fix_request(errors), from_user=False)
            return
        self._add_widget(_PlanCard(plan, errors, self._build))
        if errors:
            self._add_note("ИИ не смог исправить план сам. Опиши подробнее, что не так, или начни заново.", error=True)

    def _on_failed(self, message):
        self._set_busy(False)
        self._current.fail(message)
        if self._last_sent_text and not self._input.toPlainText().strip():
            self._input.setPlainText(self._last_sent_text)  # чтобы переотправить в один клик

    # ---- построение ----
    def _build(self, plan):
        template_paths, missing = {}, []
        for spec in plan["nodes"]:
            description = spec["template"].strip()
            if not description or TEMPLATE_PROP not in self._chat.catalog[spec["type"]]["props"]:
                continue
            path = self._template_cache.get(description)
            if path is None and self._last_screen is not None:
                saved = template_from_screen(
                    self,
                    self._last_screen,
                    description,
                    ask_path=False,
                    context=f"Шаблон для ноды «{spec['type']}» ({spec['id']})",
                )
                if saved:
                    path = self._template_cache[description] = saved[0]
            if path:
                template_paths[spec["id"]] = path
            else:
                missing.append(f"«{spec['type']}» ({spec['id']}): {description}")

        tab_title = self._builder(plan, self._chat.catalog, template_paths)
        note = f"✅ Граф построен во вкладке «{tab_title}». Проверь его перед запуском."
        if missing:
            note += (
                "\nБез шаблона (сделай кнопкой «Создать с помощью ИИ...» в ноде, когда объект будет на экране):\n- "
                + "\n- ".join(missing)
            )
        self._add_note(note)
