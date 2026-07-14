# Anti-Patterns Catalog

Comprehensive catalog of anti-patterns to catch in PySide6/Qt 6.8+ code reviews. Each entry includes detection patterns, severity, and fixes.

## Table of Contents
1. [Critical: Thread Safety](#critical-thread-safety)
2. [Critical: Memory Management](#critical-memory-management)
3. [High: Signal/Slot Issues](#high-signalslot-issues)
4. [High: Model/View Issues](#high-modelview-issues)
5. [Medium: Performance Issues](#medium-performance-issues)
6. [Medium: Qt 6 Compatibility](#medium-qt-6-compatibility)
7. [Low: Style and Conventions](#low-style-and-conventions)

---

## Critical: Thread Safety

### AP-001: GUI Access from Worker Thread
**Severity:** CRITICAL — Will crash or cause undefined behavior

**Detection:**
```python
# Look for widget methods called in QThread.run() or worker methods
class Worker(QThread):
    def run(self):
        self.label.setText("...")      # VIOLATION
        self.progress_bar.setValue(50) # VIOLATION
        self.table.setItem(...)        # VIOLATION
```

**Fix:**
```python
class Worker(QThread):
    status = Signal(str)
    progress = Signal(int)
    
    def run(self):
        self.status.emit("Working...")
        self.progress.emit(50)

# Connect in main thread
worker.status.connect(label.setText)
worker.progress.connect(progress_bar.setValue)
```

---

### AP-002: QPixmap in Non-GUI Thread
**Severity:** CRITICAL — Not thread-safe

**Detection:**
```python
class ImageWorker(QThread):
    def run(self):
        pixmap = QPixmap("image.png")  # VIOLATION
        scaled = pixmap.scaled(100, 100)
```

**Fix:**
```python
class ImageWorker(QThread):
    result = Signal(QImage)
    
    def run(self):
        image = QImage("image.png")  # Thread-safe
        scaled = image.scaled(100, 100)
        self.result.emit(scaled)

# In main thread
@Slot(QImage)
def on_image(self, img):
    pixmap = QPixmap.fromImage(img)
```

---

### AP-003: Blocking Main Thread
**Severity:** CRITICAL — Freezes UI

**Detection:**
```python
def on_button_click(self):
    time.sleep(5)                    # VIOLATION
    response = requests.get(url)     # VIOLATION
    result = subprocess.run(cmd)     # VIOLATION (without timeout)
    heavy_computation()              # VIOLATION
```

**Fix:**
```python
def on_button_click(self):
    # Use worker thread
    self.worker = Worker(self.url)
    self.worker.moveToThread(self.thread)
    self.worker.result.connect(self.on_result)
    self.thread.started.connect(self.worker.run)
    self.thread.start()

# Or use QtConcurrent
future = QtConcurrent.run(heavy_computation)
```

---

### AP-004: moveToThread with Parent
**Severity:** CRITICAL — Fails silently or crashes

**Detection:**
```python
worker = Worker(parent=self)  # Has parent
worker.moveToThread(thread)    # VIOLATION - will fail
```

**Fix:**
```python
worker = Worker()  # No parent
worker.moveToThread(thread)
# Handle cleanup via signals
worker.finished.connect(worker.deleteLater)
```

---

## Critical: Memory Management

### AP-005: Orphan QObject
**Severity:** HIGH — Memory leak

**Detection:**
```python
def create_labels(self):
    for text in texts:
        label = QLabel(text)      # VIOLATION - no parent
        self.labels.append(label)  # Stored but not parented
```

**Fix:**
```python
def create_labels(self):
    for text in texts:
        label = QLabel(text, self)  # Parent ownership
        # OR
        label = QLabel(text)
        self.layout().addWidget(label)  # Layout ownership
```

---

### AP-006: Direct QObject Deletion
**Severity:** HIGH — May crash with pending signals

**Detection:**
```python
del self.worker           # VIOLATION
self.widget = None        # VIOLATION if widget had connections
widget.__del__()          # VIOLATION
```

**Fix:**
```python
self.worker.deleteLater()  # Safe deferred deletion
# OR disconnect first
self.worker.signal.disconnect()
del self.worker
```

---

### AP-007: Lambda Signal Connection Memory Leak
**Severity:** MEDIUM — Prevents garbage collection

**Detection:**
```python
# Lambda captures self, preventing cleanup
button.clicked.connect(lambda: self.handler())
item.clicked.connect(lambda: self.process(item))  # Captures item
```

**Fix:**
```python
# Use weak reference or explicit disconnect
button.clicked.connect(self.handler)

# If lambda needed, store reference for disconnect
self._connection = button.clicked.connect(lambda: self.handler())
# Later: button.clicked.disconnect(self._connection)

# Or use functools.partial
from functools import partial
button.clicked.connect(partial(self.process, item))
```

---

### AP-008: Circular Signal References
**Severity:** MEDIUM — May prevent cleanup

**Detection:**
```python
class A(QObject):
    def __init__(self, b):
        self.b = b
        b.signal.connect(self.handler)  # A holds B, B signals A

class B(QObject):
    signal = Signal()
    def __init__(self, a):
        self.a = a  # Circular!
```

**Fix:**
```python
# Use weak references or explicit lifecycle management
import weakref

class A(QObject):
    def __init__(self, b):
        self._b_ref = weakref.ref(b)
        b.signal.connect(self.handler)
```

---

## High: Signal/Slot Issues

### AP-009: Old-Style Signal Connection
**Severity:** MEDIUM — Deprecated, error-prone

**Detection:**
```python
self.connect(button, SIGNAL("clicked()"), self.handler)  # VIOLATION
self.emit(SIGNAL("customSignal(QString)"), text)         # VIOLATION
```

**Fix:**
```python
button.clicked.connect(self.handler)
self.custom_signal.emit(text)
```

---

### AP-010: Missing @Slot Decorator
**Severity:** LOW — Works but loses optimization

**Detection:**
```python
def on_clicked(self):  # Missing @Slot()
    pass

def on_data(self, value):  # Missing @Slot(int)
    pass
```

**Fix:**
```python
@Slot()
def on_clicked(self):
    pass

@Slot(int)
def on_data(self, value: int):
    pass
```

---

### AP-011: Wrong Connection Type Cross-Thread
**Severity:** HIGH — Race condition

**Detection:**
```python
# Worker in different thread
worker.result.connect(self.handler, Qt.DirectConnection)  # VIOLATION
```

**Fix:**
```python
# Qt auto-detects, but explicit is safer
worker.result.connect(self.handler, Qt.QueuedConnection)
# Or let Qt decide
worker.result.connect(self.handler)  # Auto selects Queued
```

---

### AP-012: Signal Signature Mismatch
**Severity:** MEDIUM — Runtime error or silent failure

**Detection:**
```python
# Signal emits (int, str)
data_ready = Signal(int, str)

# Slot expects different signature
@Slot(int)  # Missing str!
def on_data(self, count):
    pass
```

**Fix:**
```python
@Slot(int, str)
def on_data(self, count: int, name: str):
    pass
```

---

## High: Model/View Issues

### AP-013: Missing begin/end Notification
**Severity:** CRITICAL — View corruption

**Detection:**
```python
def add_item(self, item):
    self._items.append(item)  # VIOLATION - no begin/end

def remove_items(self):
    self._items.clear()  # VIOLATION
```

**Fix:**
```python
def add_item(self, item):
    row = len(self._items)
    self.beginInsertRows(QModelIndex(), row, row)
    self._items.append(item)
    self.endInsertRows()

def remove_items(self):
    self.beginResetModel()
    self._items.clear()
    self.endResetModel()
```

---

### AP-014: Invalid Index Access
**Severity:** HIGH — Crash

**Detection:**
```python
def data(self, index, role):
    return self._items[index.row()].name  # VIOLATION - no validation
```

**Fix:**
```python
def data(self, index, role):
    if not index.isValid():
        return None
    if not 0 <= index.row() < len(self._items):
        return None
    if role != Qt.ItemDataRole.DisplayRole:
        return None
    return self._items[index.row()].name
```

---

### AP-015: Missing roleNames for QML
**Severity:** HIGH — QML can't access custom roles

**Detection:**
```python
class Model(QAbstractListModel):
    NameRole = Qt.UserRole + 1
    
    def data(self, index, role):
        if role == self.NameRole:
            return self._items[index.row()].name
    
    # Missing roleNames()!  # VIOLATION
```

**Fix:**
```python
def roleNames(self) -> dict:
    return {
        self.NameRole: b'name',
        self.ValueRole: b'value',
    }
```

---

### AP-016: Model Reset for Small Changes
**Severity:** MEDIUM — Poor UX (loses selection/scroll)

**Detection:**
```python
def update_item(self, row, data):
    self.beginResetModel()     # VIOLATION - overkill
    self._items[row] = data
    self.endResetModel()
```

**Fix:**
```python
def update_item(self, row, data):
    self._items[row] = data
    index = self.index(row, 0)
    self.dataChanged.emit(index, index, [])
```

---

## Medium: Performance Issues

### AP-017: Object Creation in paintEvent
**Severity:** MEDIUM — GC pressure, slower painting

**Detection:**
```python
def paintEvent(self, event):
    painter = QPainter(self)
    font = QFont("Arial", 12)  # VIOLATION
    pen = QPen(Qt.black, 2)    # VIOLATION
```

**Fix:**
```python
def __init__(self):
    self._font = QFont("Arial", 12)
    self._pen = QPen(Qt.GlobalColor.black, 2)

def paintEvent(self, event):
    painter = QPainter(self)
    painter.setFont(self._font)
    painter.setPen(self._pen)
```

---

### AP-018: repaint() Instead of update()
**Severity:** MEDIUM — Bypasses paint optimization

**Detection:**
```python
def on_data_changed(self):
    self.repaint()  # VIOLATION - forces immediate paint
```

**Fix:**
```python
def on_data_changed(self):
    self.update()  # Queued, Qt coalesces multiple calls
```

---

### AP-019: Signal Spam
**Severity:** MEDIUM — Performance degradation

**Detection:**
```python
def load_data(self, items):
    for item in items:
        self._items.append(item)
        self.itemAdded.emit(item)  # VIOLATION - 1000+ signals
```

**Fix:**
```python
def load_data(self, items):
    start = len(self._items)
    self.beginInsertRows(QModelIndex(), start, start + len(items) - 1)
    self._items.extend(items)
    self.endInsertRows()  # Single signal
```

---

### AP-020: QListWidget/QTableWidget for Large Data
**Severity:** MEDIUM — Memory and performance issues

**Detection:**
```python
# For 10000+ items
list_widget = QListWidget()
for item in large_data:
    list_widget.addItem(item.name)  # VIOLATION
```

**Fix:**
```python
# Use Model/View
model = QStringListModel([item.name for item in large_data])
list_view = QListView()
list_view.setModel(model)

# Or custom model for complex data
```

---

## Medium: Qt 6 Compatibility

### AP-021: Old Enum Syntax
**Severity:** LOW — Deprecated in Qt 6

**Detection:**
```python
Qt.AlignCenter              # VIOLATION - old style
Qt.Horizontal               # VIOLATION
Qt.LeftButton               # VIOLATION
```

**Fix:**
```python
Qt.AlignmentFlag.AlignCenter
Qt.Orientation.Horizontal
Qt.MouseButton.LeftButton
```

---

### AP-022: exec_() vs exec()
**Severity:** LOW — exec_() deprecated

**Detection:**
```python
app.exec_()        # VIOLATION
dialog.exec_()     # VIOLATION
loop.exec_()       # VIOLATION
```

**Fix:**
```python
app.exec()
dialog.exec()
loop.exec()
```

---

### AP-023: QtWidgets in QtQuick App
**Severity:** MEDIUM — Mixing paradigms incorrectly

**Detection:**
```python
# QML app using QWidgets
from PySide6.QtWidgets import QMessageBox

class QmlController(QObject):
    @Slot()
    def show_dialog(self):
        QMessageBox.information(None, "Title", "Text")  # VIOLATION
```

**Fix:**
```python
# Use QML dialogs or Qt Quick Dialogs
# Or use proper widget window as parent
@Slot()
def show_dialog(self):
    # Better: emit signal and handle in QML
    self.show_message.emit("Title", "Text")
```

---

## Low: Style and Conventions

### AP-024: camelCase Signal Names in Python
**Severity:** LOW — Style inconsistency

**Detection:**
```python
class MyClass(QObject):
    dataReady = Signal()      # VIOLATION in Python
    itemClicked = Signal(int)  # VIOLATION
```

**Fix:**
```python
class MyClass(QObject):
    data_ready = Signal()
    item_clicked = Signal(int)
    
    # Exception: QML properties need camelCase notify
    nameChanged = Signal()  # OK for QML property notify
```

---

### AP-025: Hardcoded Sizes Without DPI Awareness
**Severity:** LOW — Poor high-DPI support

**Detection:**
```python
widget.setFixedSize(400, 300)     # VIOLATION - fixed pixels
font.setPixelSize(14)              # VIOLATION - doesn't scale
```

**Fix:**
```python
widget.setMinimumSize(400, 300)  # Flexible
font.setPointSize(10)             # Scales with DPI

# Or use logical pixels
from PySide6.QtWidgets import QApplication
dpi_scale = QApplication.primaryScreen().logicalDotsPerInch() / 96
```

---

### AP-026: Missing Type Hints
**Severity:** LOW — Reduced maintainability

**Detection:**
```python
@Slot()
def process(self, data):  # Missing types
    pass

def get_items(self):  # Missing return type
    return self._items
```

**Fix:**
```python
@Slot(dict)
def process(self, data: dict) -> None:
    pass

def get_items(self) -> list[Item]:
    return self._items
```

---

## Quick Reference Checklist

### During PR Review, Check For:

**Thread Safety:**
- [ ] No GUI calls from worker threads
- [ ] No QPixmap in threads (use QImage)
- [ ] No time.sleep() or blocking calls in main thread
- [ ] moveToThread() objects have no parent

**Memory:**
- [ ] All widgets have parent or layout
- [ ] deleteLater() used for QObjects
- [ ] Lambda connections don't leak references

**Signals:**
- [ ] Modern connection syntax
- [ ] @Slot decorators present
- [ ] Signal signatures match slots

**Models:**
- [ ] begin/end brackets for all changes
- [ ] Index validation in data()
- [ ] roleNames() for QML models

**Performance:**
- [ ] No object creation in paintEvent
- [ ] update() not repaint()
- [ ] Batch model operations

**Qt 6:**
- [ ] New enum scoping
- [ ] exec() not exec_()
- [ ] Point sizes not pixel sizes
