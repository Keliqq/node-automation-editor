"""«Создать шаблон с помощью ИИ»: описание -> скриншот -> vision-модель находит
объект -> предпросмотр с подстройкой рамки -> сохранение PNG-шаблона.

Всё, кроме самого запроса к API, работает в GUI-потоке; запрос уходит в
отдельный поток, чтобы окно не зависало на несколько секунд ожидания.
"""

import os
import re
import threading
import time

import cv2
from PySide6 import QtCore, QtGui, QtWidgets

from core.ai_vision import (
    ANTHROPIC_MODEL,
    COORDS_AUTO,
    COORDS_NORMALIZED,
    COORDS_PIXELS,
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENAI,
    AiVisionError,
    coords_mode,
    count_matches,
    get_ai_settings,
    locate_object,
    set_ai_settings,
)
from core.app_paths import APP_ROOT
from core.screen import grab_monitor_bgr

TEMPLATES_DIR = os.path.join(APP_ROOT, "templates")

# Порог, с которым в предпросмотре считаются совпадения шаблона на скриншоте.
PREVIEW_THRESHOLD = 0.8

_RETRY = 2


def _to_pixmap(bgr):
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    image = QtGui.QImage(rgb.data, w, h, w * 3, QtGui.QImage.Format.Format_RGB888)
    return QtGui.QPixmap.fromImage(image.copy())


def grab_screen_hidden(monitor_index):
    """Скриншот с на время скрытыми окнами редактора — иначе ИИ увидит
    на нём сам редактор вместо игры."""
    app = QtWidgets.QApplication.instance()
    hidden = [w for w in app.topLevelWidgets() if w.isVisible()]
    for widget in hidden:
        widget.hide()
    app.processEvents()
    # Даём Windows доиграть анимацию скрытия окна.
    deadline = time.monotonic() + 0.4
    while time.monotonic() < deadline:
        app.processEvents()
        QtCore.QThread.msleep(20)
    try:
        screen, _, _ = grab_monitor_bgr(monitor_index)
    finally:
        for widget in hidden:
            widget.show()
        app.processEvents()
    return screen


class _Relay(QtCore.QObject):
    finished = QtCore.Signal(object)


def run_with_progress(parent, title, message, fn):
    """Выполняет ``fn()`` в фоновом потоке, показывая модальный прогресс
    с кнопкой «Отмена» — сетевой запрос к ИИ идёт секунды, окно не должно
    при этом висеть.

    Returns:
        результат ``fn()`` или None, если пользователь нажал «Отмена».
    Raises:
        AiVisionError: и для ошибок самой ``fn``, завёрнутых в понятный текст.
    """
    relay = _Relay()
    outcome = {}
    loop = QtCore.QEventLoop()

    def on_finished(result):
        outcome["result"] = result
        loop.quit()

    relay.finished.connect(on_finished)

    def worker():
        try:
            relay.finished.emit(fn())
        except Exception as exc:  # noqa: BLE001 — передаём в GUI-поток как есть
            relay.finished.emit(exc)

    progress = QtWidgets.QProgressDialog(message, "Отмена", 0, 0, parent)
    progress.setWindowTitle(title)
    progress.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
    progress.setMinimumDuration(0)
    progress.canceled.connect(loop.quit)
    progress.show()

    threading.Thread(target=worker, daemon=True).start()
    loop.exec()
    progress.close()

    if "result" not in outcome:
        return None  # отменено; ответ потока, если придёт, просто выбросится
    result = outcome["result"]
    if isinstance(result, AiVisionError):
        raise result
    if isinstance(result, Exception):
        raise AiVisionError(str(result)) from result
    return result


class _ScreenOverview(QtWidgets.QLabel):
    """Весь скриншот в уменьшенном виде с текущей рамкой. Протянуть мышью —
    выделить объект заново, щёлкнуть — перенести рамку центром в эту точку.
    Нужен, когда ИИ промахнулся не на пиксели, а на пол-экрана."""

    rect_selected = QtCore.Signal(int, int, int, int)

    def __init__(self, screen, max_width=560, max_height=320, parent=None):
        super().__init__(parent)
        h, w = screen.shape[:2]
        self._scale = min(max_width / w, max_height / h, 1.0)
        self._base = _to_pixmap(screen).scaled(
            max(1, int(w * self._scale)),
            max(1, int(h * self._scale)),
            QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        self._image_size = (w, h)
        self._box = None
        self._drag_start = None
        self._drag_rect = None
        self.setFixedSize(self._base.size())
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        self.setToolTip("Протяни мышью, чтобы выделить объект вручную; щелчок — перенести рамку сюда")

    def set_box(self, box):
        self._box = box
        self.update()

    def _to_image(self, point):
        w, h = self._image_size
        return (
            min(max(0, int(point.x() / self._scale)), w - 1),
            min(max(0, int(point.y() / self._scale)), h - 1),
        )

    def mousePressEvent(self, event):
        self._drag_start = event.position().toPoint()
        self._drag_rect = None

    def mouseMoveEvent(self, event):
        if self._drag_start is not None:
            self._drag_rect = QtCore.QRect(self._drag_start, event.position().toPoint()).normalized()
            self.update()

    def mouseReleaseEvent(self, event):
        if self._drag_start is None:
            return
        if self._drag_rect is not None and self._drag_rect.width() > 3 and self._drag_rect.height() > 3:
            x0, y0 = self._to_image(self._drag_rect.topLeft())
            x1, y1 = self._to_image(self._drag_rect.bottomRight())
            self.rect_selected.emit(x0, y0, max(2, x1 - x0), max(2, y1 - y0))
        elif self._box is not None:
            cx, cy = self._to_image(event.position().toPoint())
            _, _, bw, bh = self._box
            self.rect_selected.emit(max(0, cx - bw // 2), max(0, cy - bh // 2), bw, bh)
        self._drag_start = self._drag_rect = None
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.drawPixmap(0, 0, self._base)
        pen = QtGui.QPen(QtGui.QColor(255, 60, 60), 2)
        painter.setPen(pen)
        if self._drag_rect is not None:
            painter.drawRect(self._drag_rect)
        elif self._box is not None:
            x, y, w, h = self._box
            # Рамка может быть в пару пикселей — рисуем хотя бы 8x8, чтобы её было видно.
            rect = QtCore.QRectF(x * self._scale, y * self._scale, max(8, w * self._scale), max(8, h * self._scale))
            painter.drawRect(rect)


class TemplatePreviewDialog(QtWidgets.QDialog):
    """Показывает найденное: весь скриншот с рамкой (там же её можно
    выделить мышью), кусок вокруг объекта крупнее и сам шаблон. Рамку можно
    подвинуть спинбоксами — ИИ ошибается на несколько пикселей."""

    def __init__(self, screen, bbox, note, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Шаблон от ИИ — проверь")
        self._screen = screen
        sh, sw = screen.shape[:2]

        self._overview = _ScreenOverview(screen)
        self._overview.rect_selected.connect(self._set_bbox)
        self._context_label = QtWidgets.QLabel()
        self._context_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._template_label = QtWidgets.QLabel()
        self._template_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._template_label.setMinimumSize(160, 160)
        self._info_label = QtWidgets.QLabel()
        self._info_label.setWordWrap(True)

        self._spins = {}
        form = QtWidgets.QFormLayout()
        for key, label, value, maximum in (
            ("x", "x", bbox[0], sw - 1),
            ("y", "y", bbox[1], sh - 1),
            ("w", "ширина", bbox[2], sw),
            ("h", "высота", bbox[3], sh),
        ):
            spin = QtWidgets.QSpinBox()
            spin.setRange(0 if key in ("x", "y") else 2, maximum)
            spin.setValue(value)
            spin.valueChanged.connect(self._refresh)
            form.addRow(label, spin)
            self._spins[key] = spin

        note_label = QtWidgets.QLabel(f"ИИ: {note}" if note else "")
        note_label.setWordWrap(True)

        buttons = QtWidgets.QDialogButtonBox()
        buttons.addButton("Сохранить шаблон", QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        retry = buttons.addButton("Другое описание...", QtWidgets.QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton("Отмена", QtWidgets.QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        retry.clicked.connect(lambda: self.done(_RETRY))

        side = QtWidgets.QVBoxLayout()
        side.addWidget(QtWidgets.QLabel("Шаблон (увеличен):"))
        side.addWidget(self._template_label, 1)
        side.addLayout(form)
        side.addWidget(self._info_label)

        overview_hint = QtWidgets.QLabel("Весь экран — если рамка не там, выдели объект мышью:")
        overview_hint.setStyleSheet("color: gray;")
        views = QtWidgets.QVBoxLayout()
        views.addWidget(overview_hint)
        views.addWidget(self._overview, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        views.addWidget(QtWidgets.QLabel("Вблизи:"))
        views.addWidget(self._context_label, 1)

        top = QtWidgets.QHBoxLayout()
        top.addLayout(views, 3)
        top.addLayout(side, 1)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(note_label)
        layout.addLayout(top, 1)
        layout.addWidget(buttons)

        self._refresh()

    def bbox(self):
        sh, sw = self._screen.shape[:2]
        x = min(self._spins["x"].value(), sw - 2)
        y = min(self._spins["y"].value(), sh - 2)
        w = min(self._spins["w"].value(), sw - x)
        h = min(self._spins["h"].value(), sh - y)
        return x, y, w, h

    def template(self):
        x, y, w, h = self.bbox()
        return self._screen[y : y + h, x : x + w].copy()

    def _set_bbox(self, x, y, w, h):
        for key, value in zip("xywh", (x, y, w, h)):
            spin = self._spins[key]
            spin.blockSignals(True)  # одна перерисовка на все четыре значения
            spin.setValue(value)
            spin.blockSignals(False)
        self._refresh()

    def _refresh(self):
        x, y, w, h = self.bbox()
        sh, sw = self._screen.shape[:2]
        self._overview.set_box((x, y, w, h))

        # Контекст: объект с запасом по краям, чтобы было видно, что вокруг.
        pad = max(w, h) * 2 + 40
        cx0, cy0 = max(0, x - pad), max(0, y - pad)
        cx1, cy1 = min(sw, x + w + pad), min(sh, y + h + pad)
        context = self._screen[cy0:cy1, cx0:cx1].copy()
        cv2.rectangle(context, (x - cx0, y - cy0), (x - cx0 + w - 1, y - cy0 + h - 1), (60, 60, 255), 2)
        pixmap = _to_pixmap(context)
        self._context_label.setPixmap(
            pixmap.scaled(560, 420, QtCore.Qt.AspectRatioMode.KeepAspectRatio, QtCore.Qt.TransformationMode.SmoothTransformation)
            if pixmap.width() > 560 or pixmap.height() > 420
            else pixmap
        )

        template = self.template()
        zoom = max(1, min(8, 200 // max(w, h, 1)))
        self._template_label.setPixmap(
            _to_pixmap(cv2.resize(template, (w * zoom, h * zoom), interpolation=cv2.INTER_NEAREST))
        )

        matches = count_matches(self._screen, template, PREVIEW_THRESHOLD)
        self._info_label.setText(
            f"Размер {w}×{h}. Совпадений на этом скриншоте (точность {PREVIEW_THRESHOLD:g}): {matches}.\n"
            "Если объектов на экране несколько, а совпадение одно — шаблон "
            "захватывает то, что у них разное: сузь рамку."
        )


class AiSettingsDialog(QtWidgets.QDialog):
    """Выбор провайдера и его параметров для «Создать с помощью ИИ»."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки ИИ")
        settings = get_ai_settings()

        self._provider = QtWidgets.QComboBox()
        self._provider.addItem(f"Claude (Anthropic, {ANTHROPIC_MODEL})", PROVIDER_ANTHROPIC)
        self._provider.addItem("OpenAI-совместимый (OpenAI, OpenRouter, LM Studio, Ollama...)", PROVIDER_OPENAI)
        self._provider.setCurrentIndex(max(0, self._provider.findData(settings["provider"])))

        self._anthropic_key = QtWidgets.QLineEdit(settings["anthropic_api_key"])
        self._anthropic_key.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self._anthropic_key.setPlaceholderText("sk-ant-... (пусто — из ANTHROPIC_API_KEY)")
        anthropic_form = QtWidgets.QFormLayout()
        anthropic_form.addRow("Ключ API:", self._anthropic_key)
        self._anthropic_box = QtWidgets.QGroupBox("Claude")
        self._anthropic_box.setLayout(anthropic_form)

        self._base_url = QtWidgets.QLineEdit(settings["openai_base_url"])
        self._base_url.setPlaceholderText("пусто — api.openai.com; LM Studio: http://localhost:1234/v1")
        self._openai_key = QtWidgets.QLineEdit(settings["openai_api_key"])
        self._openai_key.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self._openai_key.setPlaceholderText("для локальных серверов можно оставить пустым")
        self._model = QtWidgets.QLineEdit(settings["openai_model"])
        self._model.setPlaceholderText("например: gpt-5, qwen2.5-vl-7b-instruct")
        self._max_side = QtWidgets.QSpinBox()
        self._max_side.setRange(256, 4096)
        self._max_side.setSingleStep(128)
        self._max_side.setValue(settings["openai_max_side"])
        self._max_side.setSuffix(" px")
        self._coords = QtWidgets.QComboBox()
        self._coords.addItem("Авто — определить по первому ответу", COORDS_AUTO)
        self._coords.addItem("Пиксели картинки", COORDS_PIXELS)
        self._coords.addItem("Доли 0..1000 (Qwen3-VL, Gemini)", COORDS_NORMALIZED)
        self._coords.setCurrentIndex(max(0, self._coords.findData(settings["openai_coords"])))
        self._coords_hint = QtWidgets.QLabel()
        self._coords_hint.setStyleSheet("color: gray;")
        self._coords_hint.setWordWrap(True)
        self._learned = settings["openai_coords_learned"]
        self._coords.currentIndexChanged.connect(self._update_coords_hint)
        self._model.textChanged.connect(self._update_coords_hint)
        openai_form = QtWidgets.QFormLayout()
        openai_form.addRow("Адрес (base URL):", self._base_url)
        openai_form.addRow("Ключ API:", self._openai_key)
        openai_form.addRow("Модель:", self._model)
        openai_form.addRow("Макс. сторона скриншота:", self._max_side)
        openai_form.addRow("Координаты модели:", self._coords)
        openai_form.addRow(self._coords_hint)
        self._update_coords_hint()
        self._openai_box = QtWidgets.QGroupBox("OpenAI-совместимый сервер (модель должна понимать картинки)")
        self._openai_box.setLayout(openai_form)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        form = QtWidgets.QFormLayout()
        form.addRow("Провайдер:", self._provider)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self._anthropic_box)
        layout.addWidget(self._openai_box)
        layout.addWidget(buttons)

        self._provider.currentIndexChanged.connect(self._update_visibility)
        self._update_visibility()

    def _update_visibility(self):
        is_openai = self._provider.currentData() == PROVIDER_OPENAI
        self._anthropic_box.setVisible(not is_openai)
        self._openai_box.setVisible(is_openai)
        self.adjustSize()

    def _update_coords_hint(self):
        if self._coords.currentData() != COORDS_AUTO:
            self._coords_hint.setText("")
            return
        mode = coords_mode(
            {"openai_coords": COORDS_AUTO, "openai_coords_learned": self._learned, "openai_model": self._model.text().strip()}
        )
        labels = {COORDS_PIXELS: "пиксели", COORDS_NORMALIZED: "доли 0..1000"}
        self._coords_hint.setText(
            f"Для этой модели уже определено: {labels[mode]}. Если рамки всё равно мимо — выбери формат вручную."
            if mode in labels
            else "Формат определится при первом поиске шаблона (один лишний запрос к модели)."
        )

    def values(self):
        return {
            "provider": self._provider.currentData(),
            "anthropic_api_key": self._anthropic_key.text(),
            "openai_base_url": self._base_url.text(),
            "openai_api_key": self._openai_key.text(),
            "openai_model": self._model.text(),
            "openai_max_side": self._max_side.value(),
            "openai_coords": self._coords.currentData(),
        }


def edit_ai_settings(parent=None):
    """Показывает «Настройки ИИ»; True, если пользователь сохранил."""
    dialog = AiSettingsDialog(parent)
    if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        return False
    set_ai_settings(dialog.values())
    return True


def _default_template_path(description):
    slug = re.sub(r"[^\w]+", "_", description.lower()).strip("_")[:40] or "шаблон"
    return os.path.join(TEMPLATES_DIR, f"{slug}.png")


def _unique_path(path):
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(path):
        path = f"{base}_{n}{ext}"
        n += 1
    return path


def template_from_screen(parent, screen, description, ask_path=True, context=""):
    """ИИ находит ``description`` на уже снятом ``screen`` -> предпросмотр ->
    сохранение PNG. «Другое описание...» переспрашивает описание и ищет
    заново на том же скриншоте.

    Args:
        ask_path: спросить, куда сохранить; иначе — сразу в ``templates/``
            под именем из описания (для пачки шаблонов при сборке графа).
        context: строка над полем описания (для какой ноды шаблон).

    Returns:
        tuple[str, str] | None: (путь, итоговое описание) или None, если
            отменено, объект не найден или ИИ вернул ошибку (о ней сообщено).
    """
    while True:
        try:
            located = run_with_progress(
                parent,
                "Создать шаблон с помощью ИИ",
                f"ИИ ищет на скриншоте: {description}",
                lambda: locate_object(screen, description),
            )
        except AiVisionError as exc:
            QtWidgets.QMessageBox.warning(parent, "ИИ", str(exc))
            return None
        if located is None:
            return None
        bbox, note = located
        if bbox is None:
            QtWidgets.QMessageBox.information(
                parent,
                "ИИ",
                f"«{description}» не найдено на скриншоте.\n{note}\n\n"
                "Сделай шаблон позже кнопкой «Создать с помощью ИИ...» в ноде, "
                "когда объект будет виден на экране.",
            )
            return None

        dialog = TemplatePreviewDialog(screen, bbox, note, parent)
        result = dialog.exec()
        if result == _RETRY:
            description, ok = QtWidgets.QInputDialog.getText(
                parent, "Другое описание", (context + "\n" if context else "") + "Что найти на скриншоте?", text=description
            )
            if not ok or not description.strip():
                return None
            description = description.strip()
            continue
        if result != QtWidgets.QDialog.DialogCode.Accepted:
            return None

        os.makedirs(TEMPLATES_DIR, exist_ok=True)
        if ask_path:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                parent, "Сохранить шаблон", _default_template_path(description), "PNG (*.png)"
            )
            if not path:
                return None
        else:
            path = _unique_path(_default_template_path(description))
        ok, png = cv2.imencode(".png", dialog.template())
        if not ok:
            QtWidgets.QMessageBox.warning(parent, "ИИ", "Не удалось закодировать шаблон в PNG")
            return None
        # tofile, а не cv2.imwrite: тот не умеет не-ASCII пути на Windows.
        png.tofile(path)
        return path, description


def create_template_with_ai(parent=None, monitor_index=0, description=""):
    """Сценарий кнопки «Создать с помощью ИИ...» в ноде.

    Returns:
        tuple[str, str] | None: (путь к сохранённому PNG, описание) или None,
            если пользователь отменил.
    """
    description, ok = QtWidgets.QInputDialog.getText(
        parent,
        "Создать шаблон с помощью ИИ",
        "Что найти на экране? (например: облако с предметом над героем у прилавка)\n"
        "Окно редактора на секунду скроется, чтобы сделать скриншот.",
        text=description,
    )
    description = description.strip()
    if not ok or not description:
        return None
    return template_from_screen(parent, grab_screen_hidden(monitor_index), description)
