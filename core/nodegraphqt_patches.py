"""Патчи совместимости NodeGraphQt с новыми версиями PySide6.

Импортируй этот модуль до создания любого ``PropertiesBinWidget`` или ноды,
чтобы патчи ниже успели примениться.
"""

from NodeGraphQt import BackdropNode
from NodeGraphQt.custom_widgets.properties_bin import prop_widgets_base
from PySide6 import QtWidgets

# Числовые виджеты свойств в NodeGraphQt 0.6.44 (спинбоксы int/float в
# панели «Свойства») используют устаревший плоский enum-алиас
# ``self.NoButtons``. В PySide6 6.11 этого алиаса у экземпляров
# ``QAbstractSpinBox`` больше нет, поэтому открытие свойств любой ноды с
# полем int/float (confidence, x, y, seconds, ...) падало с ``AttributeError``.
_no_buttons = QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons
setattr(prop_widgets_base.PropSpinBox, "NoButtons", _no_buttons)
setattr(prop_widgets_base.PropDoubleSpinBox, "NoButtons", _no_buttons)

# BackdropNode (встроенная в NodeGraphQt рамка-группировка) наследуется
# напрямую от NodeObject, а не от BaseNode, поэтому у неё нет метода
# ``icon()``. Панель свойств безусловно вызывает ``node.icon()``, из-за
# чего падало с AttributeError при двойном клике по Backdrop-ноде.
if not hasattr(BackdropNode, "icon"):
    setattr(BackdropNode, "icon", lambda self: None)
