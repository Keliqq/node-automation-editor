"""Приведение значений с data-входов к нужному типу.

Раньше ноды писали ``int(value or 0)`` — это ломается в обе стороны:
осознанно введённый ``0`` подменяется значением по умолчанию (точность 0
превращалась в 0.8), а «нет значения» (``None`` с выхода, который ничего
не нашёл) молча становится нулём — и «Клик» улетал в левый верхний угол
экрана. Здесь «пусто» — это только ``None`` и пустая строка, а нечисловой
текст — ошибка, а не тихий ноль.
"""


class MissingValueError(ValueError):
    """На вход пришло «ничего» там, где без значения работать нельзя."""


def is_missing(value):
    return value is None or (isinstance(value, str) and not value.strip())


def to_float(value, default=None):
    """``None``/пустая строка -> ``default`` (или ``MissingValueError``, если
    ``default`` не задан). Запятая допускается как десятичный разделитель."""
    if is_missing(value):
        if default is None:
            raise MissingValueError("значение не задано")
        return float(default)
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"не число: {value!r}") from None


def to_int(value, default=None):
    return int(round(to_float(value, default)))


def to_bool(value):
    """Строки «false»/«0»/«нет»/«ложь» из текстового свойства — это ложь,
    а не «непустая строка, значит истина»."""
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "нет", "ложь")
    return bool(value)
