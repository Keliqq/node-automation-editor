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
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENAI,
    AiVisionError,
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


def _grab_screen_hidden(monitor_index):
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


def _locate_with_progress(parent, screen, description):
    """Запускает ``locate_object`` в фоне, показывая модальный прогресс.

    Returns:
        (bbox | None, note) или None, если пользователь нажал «Отмена».
    Raises:
        AiVisionError
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
            relay.finished.emit(locate_object(screen, description))
        except Exception as exc:  # noqa: BLE001 — передаём в GUI-поток как есть
            relay.finished.emit(exc)

    progress = QtWidgets.QProgressDialog("ИИ ищет объект на скриншоте...", "Отмена", 0, 0, parent)
    progress.setWindowTitle("Создать шаблон с помощью ИИ")
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


class TemplatePreviewDialog(QtWidgets.QDialog):
    """Показывает найденное: кусок скриншота вокруг объекта с красной
    рамкой и сам шаблон крупно. Рамку можно подвинуть спинбоксами — ИИ
    ошибается на несколько пикселей."""

    def __init__(self, screen, bbox, note, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Шаблон от ИИ — проверь")
        self._screen = screen
        sh, sw = screen.shape[:2]

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

        top = QtWidgets.QHBoxLayout()
        top.addWidget(self._context_label, 3)
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

    def _refresh(self):
        x, y, w, h = self.bbox()
        sh, sw = self._screen.shape[:2]

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
        self._normalized = QtWidgets.QCheckBox("Модель отвечает в координатах 0..1000 (Qwen3-VL, Gemini)")
        self._normalized.setChecked(settings["openai_normalized"])
        openai_form = QtWidgets.QFormLayout()
        openai_form.addRow("Адрес (base URL):", self._base_url)
        openai_form.addRow("Ключ API:", self._openai_key)
        openai_form.addRow("Модель:", self._model)
        openai_form.addRow("Макс. сторона скриншота:", self._max_side)
        openai_form.addRow(self._normalized)
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

    def values(self):
        return {
            "provider": self._provider.currentData(),
            "anthropic_api_key": self._anthropic_key.text(),
            "openai_base_url": self._base_url.text(),
            "openai_api_key": self._openai_key.text(),
            "openai_model": self._model.text(),
            "openai_max_side": self._max_side.value(),
            "openai_normalized": self._normalized.isChecked(),
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


def create_template_with_ai(parent=None, monitor_index=0, description=""):
    """Полный сценарий создания шаблона.

    Returns:
        tuple[str, str] | None: (путь к сохранённому PNG, описание) или None,
            если пользователь отменил.
    """
    while True:
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

        screen = _grab_screen_hidden(monitor_index)
        try:
            located = _locate_with_progress(parent, screen, description)
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
                f"Объект не найден на скриншоте.\n{note}\n\n"
                "Убедись, что он виден на экране, и попробуй ещё раз.",
            )
            continue

        dialog = TemplatePreviewDialog(screen, bbox, note, parent)
        result = dialog.exec()
        if result == _RETRY:
            continue
        if result != QtWidgets.QDialog.DialogCode.Accepted:
            return None

        os.makedirs(TEMPLATES_DIR, exist_ok=True)
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            parent, "Сохранить шаблон", _default_template_path(description), "PNG (*.png)"
        )
        if not path:
            return None
        ok, png = cv2.imencode(".png", dialog.template())
        if not ok:
            QtWidgets.QMessageBox.warning(parent, "ИИ", "Не удалось закодировать шаблон в PNG")
            return None
        # tofile, а не cv2.imwrite: тот не умеет не-ASCII пути на Windows.
        png.tofile(path)
        return path, description
