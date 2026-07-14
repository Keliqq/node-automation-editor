"""Глобальный хоткей аварийной остановки.

Работает даже когда окно редактора не в фокусе — сама автоматизация двигает
мышь и эмулирует клавиши поверх других окон, так что кликнуть «Остановить»
в интерфейсе может быть банально не на чем. ``keyboard`` ставит системный
хук и зовёт колбэк из своего собственного потока, а не из потока Qt, поэтому
колбэк должен быть просто ``signal.emit()`` — Qt сам поставит вызов слота в
очередь GUI-потока (сигнал/слот между потоками потокобезопасны сами по себе,
в отличие от прямого обращения к виджетам из чужого потока).

Известное ограничение ОС (Windows UIPI): если игра/приложение под курсором
запущено с более высокими правами (администратор), а сам редактор — нет,
хук не увидит нажатие клавиши в её окне. Обойти можно, только запустив
редактор тоже от администратора.
"""

from PySide6.QtCore import QSettings

_SETTINGS_ORG = "NodeAutomationEditor"
_SETTINGS_APP = "NodeEditor"
_SETTINGS_KEY = "stop_hotkey"

DEFAULT_HOTKEY = "f9"


def _settings():
    return QSettings(_SETTINGS_ORG, _SETTINGS_APP)


def get_stop_hotkey():
    return _settings().value(_SETTINGS_KEY, DEFAULT_HOTKEY, type=str)


def set_stop_hotkey(hotkey):
    _settings().setValue(_SETTINGS_KEY, hotkey or DEFAULT_HOTKEY)


class HotkeyManager:
    """Тонкая обёртка над ``keyboard``: позволяет переназначить хоткей на
    лету (снять старый хук, поставить новый) и корректно снять его при
    закрытии приложения. Импортирует ``keyboard`` лениво и ловит любые его
    ошибки — на системах без поддержки (например, Linux без root) паникующая
    кнопка просто не работает, но само приложение не должно из-за этого
    падать."""

    def __init__(self, callback):
        self._callback = callback
        self._current = None

    def apply(self, hotkey=None):
        """Регистрирует новый хоткей, снимая предыдущий. Бросает
        ``RuntimeError`` с человекочитаемым сообщением, если не вышло —
        вызывающий код решает, как это показать пользователю."""
        hotkey = (hotkey or DEFAULT_HOTKEY).strip().lower()
        try:
            import keyboard
        except Exception as exc:
            raise RuntimeError(f"Библиотека keyboard недоступна: {exc}") from exc

        self.unregister()
        try:
            keyboard.add_hotkey(hotkey, self._callback)
        except (ValueError, ImportError, OSError) as exc:
            raise RuntimeError(f"Не удалось зарегистрировать хоткей «{hotkey}»: {exc}") from exc
        self._current = hotkey

    def unregister(self):
        if not self._current:
            return
        try:
            import keyboard

            keyboard.remove_hotkey(self._current)
        except Exception:
            pass
        self._current = None
