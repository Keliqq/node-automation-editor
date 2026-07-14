# Signal/Slot Patterns Reference

## Table of Contents
1. [Signal Declaration](#signal-declaration)
2. [Connection Types](#connection-types)
3. [Thread-Safe Patterns](#thread-safe-patterns)
4. [Common Mistakes](#common-mistakes)
5. [Advanced Patterns](#advanced-patterns)

---

## Signal Declaration

### Basic Signals
```python
from PySide6.QtCore import QObject, Signal, Slot

class MyClass(QObject):
    # No arguments
    triggered = Signal()
    
    # Single typed argument
    value_changed = Signal(int)
    text_changed = Signal(str)
    
    # Multiple arguments
    position_changed = Signal(int, int)
    data_ready = Signal(str, dict, list)
    
    # Overloaded signals (different signatures)
    updated = Signal((int,), (str,))  # Can emit int OR str
    
    # Object passing
    item_selected = Signal(object)  # Any Python object
    model_changed = Signal("QAbstractItemModel")  # Forward reference
```

### Qt 6.8+ Signal Naming
```python
# PEP8 snake_case for Python signals
class Modern(QObject):
    data_loaded = Signal(dict)      # Correct
    # dataLoaded = Signal(dict)     # Avoid camelCase in Python
    
    # Exception: matching Qt signals for QML
    countChanged = Signal()  # QML expects camelCase for property notify
```

---

## Connection Types

### Automatic (Default)
```python
# Qt determines connection type based on thread affinity
sender.signal.connect(receiver.slot)  # Auto: Direct or Queued
```

### Explicit Connection Types
```python
from PySide6.QtCore import Qt

# Direct: Slot called immediately in sender's thread (DANGEROUS cross-thread)
sender.signal.connect(receiver.slot, Qt.ConnectionType.DirectConnection)

# Queued: Slot called in receiver's thread event loop (THREAD-SAFE)
sender.signal.connect(receiver.slot, Qt.ConnectionType.QueuedConnection)

# Blocking Queued: Waits for slot to complete (USE SPARINGLY - can deadlock)
sender.signal.connect(receiver.slot, Qt.ConnectionType.BlockingQueuedConnection)

# Unique: Prevents duplicate connections
sender.signal.connect(receiver.slot, Qt.ConnectionType.UniqueConnection)

# Combine flags
sender.signal.connect(
    receiver.slot,
    Qt.ConnectionType.QueuedConnection | Qt.ConnectionType.UniqueConnection
)
```

### Connection Syntax Variants
```python
# Method reference (preferred)
button.clicked.connect(self.on_click)

# Lambda (for arguments)
button.clicked.connect(lambda: self.handle_click(42))
button.clicked.connect(lambda checked: self.toggle(checked))

# Partial function
from functools import partial
button.clicked.connect(partial(self.handler, "arg1", "arg2"))

# Free function
button.clicked.connect(standalone_function)
```

### Disconnection
```python
# Disconnect specific slot
button.clicked.disconnect(self.on_click)

# Disconnect all slots from signal
button.clicked.disconnect()

# Disconnect in cleanup
def closeEvent(self, event):
    self.worker.progress.disconnect(self.update_progress)
    super().closeEvent(event)
```

---

## Thread-Safe Patterns

### Cross-Thread Signal Emission
```python
class Worker(QObject):
    # Signals are ALWAYS thread-safe to emit
    progress = Signal(int)
    result = Signal(object)
    error = Signal(str)
    
    @Slot()
    def process(self):
        try:
            for i in range(100):
                # Safe from any thread
                self.progress.emit(i)
            self.result.emit(self.data)
        except Exception as e:
            self.error.emit(str(e))

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.thread = QThread()
        self.worker = Worker()
        self.worker.moveToThread(self.thread)
        
        # Connections are automatically queued (cross-thread)
        self.worker.progress.connect(self.on_progress)  # Queued auto
        self.worker.result.connect(self.on_result)
        self.worker.error.connect(self.on_error)
        
        self.thread.started.connect(self.worker.process)
        self.worker.result.connect(self.thread.quit)
    
    @Slot(int)
    def on_progress(self, value: int):
        self.progress_bar.setValue(value)  # Safe: called in main thread
```

### Signal-Only Communication Pattern
```python
# Controller emits signals, never calls worker methods directly
class Controller(QObject):
    start_work = Signal(dict)    # Parameters to worker
    cancel_work = Signal()
    
class Worker(QObject):
    finished = Signal(object)
    
    def __init__(self):
        super().__init__()
        self._cancelled = False
    
    @Slot(dict)
    def do_work(self, params: dict):
        for item in params['items']:
            if self._cancelled:
                return
            # process...
        self.finished.emit(result)
    
    @Slot()
    def cancel(self):
        self._cancelled = True

# Setup
controller = Controller()
worker = Worker()
worker.moveToThread(thread)
controller.start_work.connect(worker.do_work, Qt.QueuedConnection)
controller.cancel_work.connect(worker.cancel, Qt.QueuedConnection)
```

---

## Common Mistakes

### ❌ Missing @Slot Decorator
```python
# WRONG: May work but loses type checking and optimization
def on_click(self):
    pass

# CORRECT: Always decorate slots
@Slot()
def on_click(self):
    pass

@Slot(int, str)
def on_data(self, count: int, name: str):
    pass
```

### ❌ Lambda Capturing Loop Variable
```python
# WRONG: All buttons call handler(4)
for i in range(5):
    btn = QPushButton(str(i))
    btn.clicked.connect(lambda: self.handler(i))

# CORRECT: Capture by value
for i in range(5):
    btn = QPushButton(str(i))
    btn.clicked.connect(lambda checked, x=i: self.handler(x))
```

### ❌ Connecting to Destroyed Object
```python
# WRONG: object may be deleted, dangling connection
temp_widget.signal.connect(self.handler)
temp_widget.deleteLater()  # Connection now invalid

# CORRECT: Disconnect before deletion or use parent relationship
temp_widget.signal.connect(self.handler)
# Later...
temp_widget.signal.disconnect(self.handler)
temp_widget.deleteLater()
```

### ❌ Signal Signature Mismatch
```python
class Emitter(QObject):
    data_ready = Signal(str, int)

# WRONG: Slot signature doesn't match
@Slot(str)  # Missing int
def on_data(self, text):
    pass

# CORRECT: Match signature
@Slot(str, int)
def on_data(self, text: str, count: int):
    pass

# ALSO CORRECT: Accept fewer args (extras ignored)
@Slot()
def on_data(self):  # Just notified, don't need data
    pass
```

### ❌ Circular Signal Dependencies
```python
# WRONG: Infinite loop
self.spinbox1.valueChanged.connect(self.spinbox2.setValue)
self.spinbox2.valueChanged.connect(self.spinbox1.setValue)

# CORRECT: Block signals during sync
def sync_spinboxes(self, value):
    self.spinbox2.blockSignals(True)
    self.spinbox2.setValue(value)
    self.spinbox2.blockSignals(False)
```

---

## Advanced Patterns

### Custom Signal with Validation
```python
class ValidatedEmitter(QObject):
    value_changed = Signal(int)
    
    def __init__(self):
        super().__init__()
        self._value = 0
    
    @property
    def value(self):
        return self._value
    
    @value.setter
    def value(self, new_value: int):
        if new_value != self._value:
            self._value = new_value
            self.value_changed.emit(new_value)
```

### Signal Aggregator
```python
class SignalAggregator(QObject):
    """Combines multiple signals into one with debouncing."""
    aggregated = Signal(list)
    
    def __init__(self, debounce_ms: int = 100):
        super().__init__()
        self._pending = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(debounce_ms)
        self._timer.timeout.connect(self._emit)
    
    @Slot(object)
    def add(self, item):
        self._pending.append(item)
        self._timer.start()
    
    def _emit(self):
        if self._pending:
            self.aggregated.emit(self._pending.copy())
            self._pending.clear()
```

### Signal Spy (Testing)
```python
class SignalSpy(QObject):
    """Records signal emissions for testing."""
    
    def __init__(self, signal):
        super().__init__()
        self._emissions = []
        signal.connect(self._record)
    
    @Slot(object)
    def _record(self, *args):
        self._emissions.append(args)
    
    @property
    def count(self):
        return len(self._emissions)
    
    def wait(self, timeout_ms: int = 5000) -> bool:
        """Block until signal emitted or timeout."""
        loop = QEventLoop()
        QTimer.singleShot(timeout_ms, loop.quit)
        if not self._emissions:
            loop.exec()
        return len(self._emissions) > 0
```

### Property Notification (Qt 6.8+ / QML)
```python
from PySide6.QtCore import Property

class Model(QObject):
    name_changed = Signal()
    
    def __init__(self):
        super().__init__()
        self._name = ""
    
    def get_name(self):
        return self._name
    
    def set_name(self, value: str):
        if self._name != value:
            self._name = value
            self.name_changed.emit()
    
    # QML-compatible property
    name = Property(str, get_name, set_name, notify=name_changed)
```
