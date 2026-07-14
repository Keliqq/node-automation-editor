# -*- mode: python ; coding: utf-8 -*-
"""Сборка PyInstaller: ``pyinstaller node_editor.spec``.

Собираем в режиме onedir (папка, а не один файл) — так проще положить
рядом с exe редактируемые ``examples/``/``user_nodes/`` (см.
``core/app_paths.py``: в frozen-режиме они ищутся рядом с exe, а не во
временной папке распаковки). После сборки скопируй эти две папки из
корня репозитория в ``dist/NodeAutomationEditor/`` вручную (или через
build-скрипт, если появится).

``PySide6.QtSvg``/``QtSvgWidgets`` — в hiddenimports, потому что
NodeGraphQt подключает их через динамический шим ``Qt.py``, а не прямым
импортом, и статический анализ PyInstaller их не находит сам.
"""

import os

from NodeGraphQt import __file__ as nodegraphqt_init

block_cipher = None

nodegraphqt_dir = os.path.dirname(nodegraphqt_init)
nodegraphqt_icon = os.path.join(nodegraphqt_dir, "widgets", "icons", "node_base.png")

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        (nodegraphqt_icon, "NodeGraphQt/widgets/icons"),
    ],
    hiddenimports=[
        "PySide6.QtSvg",
        "PySide6.QtSvgWidgets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NodeAutomationEditor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="NodeAutomationEditor",
)
