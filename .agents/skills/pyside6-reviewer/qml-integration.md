# QML Integration Reference

## Table of Contents
1. [Python-QML Bridge Basics](#python-qml-bridge-basics)
2. [Property System](#property-system)
3. [Type Registration](#type-registration)
4. [Context Properties](#context-properties)
5. [Signals Between Python and QML](#signals-between-python-and-qml)
6. [Models for QML](#models-for-qml)
7. [Qt 6.8+ Declarative Registration](#qt-68-declarative-registration)
8. [Common Mistakes](#common-mistakes)

---

## Python-QML Bridge Basics

### Minimal QML Application
```python
import sys
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

if __name__ == "__main__":
    app = QGuiApplication(sys.argv)
    
    engine = QQmlApplicationEngine()
    engine.load("main.qml")
    
    if not engine.rootObjects():
        sys.exit(-1)
    
    sys.exit(app.exec())
```

```qml
// main.qml
import QtQuick
import QtQuick.Controls

ApplicationWindow {
    visible: true
    width: 640
    height: 480
    title: "My App"
    
    Button {
        text: "Click Me"
        anchors.centerIn: parent
        onClicked: console.log("Clicked!")
    }
}
```

### Loading QML from Resources
```python
from PySide6.QtCore import QUrl

# From file
engine.load(QUrl.fromLocalFile("main.qml"))

# From Qt resources (compiled .qrc)
engine.load(QUrl("qrc:/qml/main.qml"))

# From string (debugging only)
component = QQmlComponent(engine)
component.setData(b"""
    import QtQuick
    Rectangle { width: 100; height: 100; color: "red" }
""", QUrl())
```

---

## Property System

### Basic QML-Accessible Properties
```python
from PySide6.QtCore import QObject, Property, Signal, Slot

class Backend(QObject):
    # Notify signal (required for QML binding updates)
    name_changed = Signal()
    count_changed = Signal()
    
    def __init__(self):
        super().__init__()
        self._name = ""
        self._count = 0
    
    # Getter
    def get_name(self) -> str:
        return self._name
    
    # Setter
    def set_name(self, value: str):
        if self._name != value:
            self._name = value
            self.name_changed.emit()
    
    # Property declaration
    name = Property(str, get_name, set_name, notify=name_changed)
    
    # Read-only property
    def get_count(self) -> int:
        return self._count
    
    count = Property(int, get_count, notify=count_changed)
    
    # Method callable from QML
    @Slot()
    def increment(self):
        self._count += 1
        self.count_changed.emit()
    
    @Slot(str, result=str)
    def process(self, text: str) -> str:
        return text.upper()
```

### Pythonic Property Decorator (Qt 6+)
```python
from PySide6.QtCore import QObject, Signal
from PySide6.QtQml import QmlElement

# Qt 6.8+ style with decorator
class ModernBackend(QObject):
    name_changed = Signal()
    
    def __init__(self):
        super().__init__()
        self._name = ""
    
    @Property(str, notify=name_changed)
    def name(self) -> str:
        return self._name
    
    @name.setter
    def name(self, value: str):
        if self._name != value:
            self._name = value
            self.name_changed.emit()
```

### Complex Property Types
```python
from PySide6.QtCore import QObject, Property, Signal
from PySide6.QtQml import QmlElement

class DataModel(QObject):
    data_changed = Signal()
    
    def __init__(self):
        super().__init__()
        self._items = []
        self._config = {}
    
    # List property (converts to JS array)
    @Property(list, notify=data_changed)
    def items(self) -> list:
        return self._items
    
    @items.setter
    def items(self, value: list):
        self._items = value
        self.data_changed.emit()
    
    # Dict property (converts to JS object)
    @Property("QVariantMap", notify=data_changed)
    def config(self) -> dict:
        return self._config
    
    @config.setter
    def config(self, value: dict):
        self._config = value
        self.data_changed.emit()
```

---

## Type Registration

### Qt 6.8+ Declarative Registration (Recommended)
```python
from PySide6.QtCore import QObject, Property, Signal, Slot
from PySide6.QtQml import QmlElement, QmlSingleton

# Register as instantiable type
QML_IMPORT_NAME = "MyApp"
QML_IMPORT_MAJOR_VERSION = 1

@QmlElement
class Counter(QObject):
    countChanged = Signal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._count = 0
    
    @Property(int, notify=countChanged)
    def count(self) -> int:
        return self._count
    
    @Slot()
    def increment(self):
        self._count += 1
        self.countChanged.emit()

# Register as singleton
@QmlElement
@QmlSingleton
class AppSettings(QObject):
    theme_changed = Signal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._theme = "light"
    
    @Property(str, notify=theme_changed)
    def theme(self) -> str:
        return self._theme
    
    @theme.setter
    def theme(self, value: str):
        if self._theme != value:
            self._theme = value
            self.theme_changed.emit()
```

```qml
// Usage in QML
import MyApp 1.0

ApplicationWindow {
    Counter {
        id: counter
    }
    
    Text {
        text: counter.count
    }
    
    // Singleton access
    Text {
        text: AppSettings.theme
    }
}
```

### Manual Registration (Legacy)
```python
from PySide6.QtQml import qmlRegisterType, qmlRegisterSingletonType

# Register type
qmlRegisterType(Counter, "MyApp", 1, 0, "Counter")

# Register singleton with factory
def settings_factory(engine, script_engine):
    return AppSettings()

qmlRegisterSingletonType(AppSettings, "MyApp", 1, 0, "AppSettings", settings_factory)
```

---

## Context Properties

### Setting Context Properties
```python
class Application:
    def __init__(self):
        self.app = QGuiApplication(sys.argv)
        self.engine = QQmlApplicationEngine()
        
        # Create backend objects
        self.backend = Backend()
        self.user_model = UserModel()
        
        # Expose to QML context
        context = self.engine.rootContext()
        context.setContextProperty("backend", self.backend)
        context.setContextProperty("userModel", self.user_model)
        
        # Load QML after setting context
        self.engine.load("main.qml")
```

```qml
// Access in QML
Text {
    text: backend.name
}

ListView {
    model: userModel
}
```

### Context Object (Single Root)
```python
# Expose single object as "root" context
class AppContext(QObject):
    def __init__(self):
        super().__init__()
        self.settings = Settings()
        self.user = UserManager()
        self.api = ApiClient()

context = AppContext()
engine.rootContext().setContextObject(context)
```

---

## Signals Between Python and QML

### Python to QML
```python
class Notifier(QObject):
    # Signal with data
    message_received = Signal(str, arguments=['message'])
    data_updated = Signal(dict, arguments=['data'])
    
    def send_message(self, msg: str):
        self.message_received.emit(msg)
```

```qml
Connections {
    target: notifier
    
    function onMessage_received(message) {
        console.log("Got:", message)
    }
    
    function onData_updated(data) {
        console.log("Data:", JSON.stringify(data))
    }
}
```

### QML to Python
```python
class Handler(QObject):
    @Slot(str)
    def handle_click(self, item_id: str):
        print(f"Clicked: {item_id}")
    
    @Slot(str, str, result=bool)
    def validate(self, field: str, value: str) -> bool:
        return len(value) > 0
```

```qml
Button {
    onClicked: handler.handle_click("btn_1")
}

TextField {
    onTextChanged: {
        if (!handler.validate("email", text)) {
            // Show error
        }
    }
}
```

---

## Models for QML

### QAbstractListModel for QML
```python
from PySide6.QtCore import QAbstractListModel, Qt, QModelIndex

class TodoModel(QAbstractListModel):
    # Role enum
    TitleRole = Qt.ItemDataRole.UserRole + 1
    CompletedRole = Qt.ItemDataRole.UserRole + 2
    IdRole = Qt.ItemDataRole.UserRole + 3
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
    
    def rowCount(self, parent=QModelIndex()):
        return len(self._items)
    
    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        
        item = self._items[index.row()]
        
        if role == self.TitleRole:
            return item['title']
        elif role == self.CompletedRole:
            return item['completed']
        elif role == self.IdRole:
            return item['id']
        return None
    
    # CRITICAL: Required for QML role access
    def roleNames(self) -> dict:
        return {
            self.TitleRole: b'title',
            self.CompletedRole: b'completed',
            self.IdRole: b'itemId',
        }
    
    # Slots for QML interaction
    @Slot(str)
    def addItem(self, title: str):
        self.beginInsertRows(QModelIndex(), len(self._items), len(self._items))
        self._items.append({
            'id': str(uuid.uuid4()),
            'title': title,
            'completed': False
        })
        self.endInsertRows()
    
    @Slot(int)
    def toggleItem(self, row: int):
        if 0 <= row < len(self._items):
            self._items[row]['completed'] = not self._items[row]['completed']
            idx = self.index(row, 0)
            self.dataChanged.emit(idx, idx, [self.CompletedRole])
```

```qml
ListView {
    model: todoModel
    
    delegate: ItemDelegate {
        text: model.title
        checkable: true
        checked: model.completed
        onClicked: todoModel.toggleItem(index)
    }
}
```

### QML ListModel Alternative (Simple Cases)
```python
@Slot(result="QVariantList")
def get_items(self) -> list:
    """Return list of dicts for simple QML ListModel."""
    return [
        {"name": "Item 1", "value": 100},
        {"name": "Item 2", "value": 200},
    ]
```

```qml
// Simple list without custom model
ListView {
    model: backend.get_items()
    delegate: Text { text: modelData.name }
}
```

---

## Qt 6.8+ Declarative Registration

### Full Example with All Features
```python
# types.py
from PySide6.QtCore import QObject, Property, Signal, Slot, QUrl
from PySide6.QtQml import QmlElement, QmlSingleton, QmlNamedElement

QML_IMPORT_NAME = "App.Core"
QML_IMPORT_MAJOR_VERSION = 1
QML_IMPORT_MINOR_VERSION = 0

@QmlElement
class FileHandler(QObject):
    """Instantiable type for file operations."""
    
    file_loaded = Signal(str, arguments=['content'])
    error_occurred = Signal(str, arguments=['message'])
    
    @Slot(QUrl)
    def load_file(self, url: QUrl):
        try:
            path = url.toLocalFile()
            with open(path, 'r') as f:
                self.file_loaded.emit(f.read())
        except Exception as e:
            self.error_occurred.emit(str(e))

@QmlNamedElement("AppConfig")  # Custom QML name
class ApplicationConfiguration(QObject):
    """Type with custom QML name."""
    pass

@QmlElement
@QmlSingleton
class Theme(QObject):
    """Singleton for theming."""
    
    changed = Signal()
    
    def __init__(self):
        super().__init__()
        self._dark = False
    
    @Property(bool, notify=changed)
    def dark(self) -> bool:
        return self._dark
    
    @dark.setter
    def dark(self, value: bool):
        if self._dark != value:
            self._dark = value
            self.changed.emit()
    
    @Property(str, notify=changed)
    def backgroundColor(self) -> str:
        return "#1e1e1e" if self._dark else "#ffffff"
    
    @Property(str, notify=changed)
    def textColor(self) -> str:
        return "#ffffff" if self._dark else "#000000"
```

```qml
import App.Core 1.0

ApplicationWindow {
    color: Theme.backgroundColor
    
    FileHandler {
        id: fileHandler
        onFile_loaded: (content) => textArea.text = content
        onError_occurred: (message) => errorDialog.open(message)
    }
    
    Button {
        text: "Load"
        onClicked: fileHandler.load_file(fileDialog.fileUrl)
    }
}
```

---

## Common Mistakes

### ❌ Missing roleNames() Override
```python
# WRONG: Roles not accessible in QML
class BadModel(QAbstractListModel):
    NameRole = Qt.ItemDataRole.UserRole + 1
    
    def data(self, index, role):
        if role == self.NameRole:
            return self._items[index.row()].name
    # Missing roleNames() - QML can't access NameRole!

# CORRECT: Always implement roleNames
def roleNames(self) -> dict:
    return {
        self.NameRole: b'name',  # Bytes, not str!
    }
```

### ❌ Wrong Notify Signal
```python
# WRONG: Property won't update in QML
class Bad(QObject):
    some_signal = Signal()
    
    @Property(str, notify=some_signal)  # Wrong signal!
    def name(self):
        return self._name
    
    @name.setter
    def name(self, v):
        self._name = v
        # Forgot to emit!

# CORRECT: Emit the notify signal
class Good(QObject):
    name_changed = Signal()
    
    @Property(str, notify=name_changed)
    def name(self):
        return self._name
    
    @name.setter
    def name(self, v):
        if self._name != v:
            self._name = v
            self.name_changed.emit()  # Emit on change
```

### ❌ Slot Without Return Type
```python
# WRONG: No return type annotation
@Slot(str)
def get_result(self, key):
    return self.data[key]  # QML gets undefined!

# CORRECT: Specify result type
@Slot(str, result=str)
def get_result(self, key: str) -> str:
    return self.data[key]

@Slot(result="QVariantList")
def get_list(self) -> list:
    return [1, 2, 3]
```

### ❌ Context Property After Load
```python
# WRONG: Setting context after loading QML
engine.load("main.qml")
engine.rootContext().setContextProperty("backend", backend)  # Too late!

# CORRECT: Set context before loading
engine.rootContext().setContextProperty("backend", backend)
engine.load("main.qml")
```

### ❌ Python Signal Name vs QML Handler
```python
# Python signal
data_loaded = Signal(dict)  # snake_case

# QML handler - Qt converts to camelCase with 'on' prefix
Connections {
    // WRONG
    function ondata_loaded(data) {}  // Won't work
    
    // CORRECT - Qt 6 style
    function onData_loaded(data) {}  // Works
}
```

### ❌ Modifying Model Without Notification
```python
# WRONG: View won't update
@Slot(int, str)
def update_item(self, row, name):
    self._items[row].name = name  # No notification!

# CORRECT: Emit dataChanged
@Slot(int, str)
def update_item(self, row: int, name: str):
    self._items[row].name = name
    idx = self.index(row, 0)
    self.dataChanged.emit(idx, idx, [self.NameRole])
```
