"""Настройка Tesseract OCR и чтение текста с экрана.

Автоматически определяет стандартную установку Tesseract-OCR на Windows;
если он стоит в другом месте, путь можно переопределить в меню приложения
«Настройки» (сохраняется через QSettings, задать нужно один раз).
"""

import os

import cv2
import pytesseract
from PySide6.QtCore import QSettings

_SETTINGS_ORG = "NodeAutomationEditor"
_SETTINGS_APP = "NodeEditor"
_SETTINGS_KEY = "tesseract_path"

_DEFAULT_CANDIDATES = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]


def _settings():
    return QSettings(_SETTINGS_ORG, _SETTINGS_APP)


def get_tesseract_path():
    """Текущий заданный вручную путь к tesseract.exe, или '' если
    используется автоопределение / PATH."""
    return _settings().value(_SETTINGS_KEY, "", type=str)


def set_tesseract_path(path):
    _settings().setValue(_SETTINGS_KEY, path or "")
    apply_tesseract_path()


def apply_tesseract_path():
    """Указывает pytesseract на нужный tesseract.exe: сначала путь,
    заданный пользователем, иначе — первое найденное стандартное место
    установки."""
    custom = get_tesseract_path()
    if custom:
        pytesseract.pytesseract.tesseract_cmd = custom
        return
    for candidate in _DEFAULT_CANDIDATES:
        if os.path.isfile(candidate):
            pytesseract.pytesseract.tesseract_cmd = candidate
            return


apply_tesseract_path()


def read_text(image_rgb, digits_only=False, lang="eng"):
    """Распознаёт текст на небольшом кадре экрана (RGB-массив numpy).
    Мелкий HUD-шрифт сначала увеличивается (Tesseract нужны символы
    примерно от 30 px в высоту); увеличение уже читаемого текста только
    размывает его и портит точность, поэтому пропускается, если кадр и
    так достаточно крупный."""
    gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
    height = gray.shape[0]
    if height < 40:
        scale = max(1, round(120 / height))
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    config = "--psm 7"
    if digits_only:
        config += " -c tessedit_char_whitelist=0123456789.,-"

    text = pytesseract.image_to_string(thresh, lang=lang, config=config)
    return text.strip()
