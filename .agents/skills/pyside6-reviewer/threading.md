# Threading Patterns Reference

## Table of Contents
1. [Thread Safety Fundamentals](#thread-safety-fundamentals)
2. [QThread Patterns](#qthread-patterns)
3. [Worker Object Pattern](#worker-object-pattern)
4. [QtConcurrent](#qtconcurrent)
5. [Thread Pools](#thread-pools)
6. [Async Integration (Qt 6.8+)](#async-integration)
7. [Thread-Safe Data Structures](#thread-safe-data-structures)
8. [Common Mistakes](#common-mistakes)

---

## Thread Safety Fundamentals

### Golden Rules
1. **GUI operations ONLY on main thread** — Never touch widgets from worker threads
2. **Signals are thread-safe** — Emit from any thread, Qt handles delivery
3. **Use QueuedConnection for cross-thread** — Automatic when receiver is in different thread
4. **QObject has thread affinity** — Lives in one thread, determined at creation or `moveToThread()`

### What's Safe Across Threads
```python
# SAFE from any thread:
signal.emit(data)              # Signal emission
QMetaObject.invokeMethod(...)  # Invoke slot in object's thread
QCoreApplication.postEvent()   # Post event to object's thread
Qt.thread-safe classes         # QMutex, QReadWriteLock, QSemaphore, QWaitCondition

# NOT SAFE (main thread only):
widget.setText("...")          # Any widget method
widget.show() / hide()         # Visibility changes
widget.setStyleSheet(...)      # Style changes
layout.addWidget(...)          # Layout operations
QPixmap operations             # (use QImage in threads)
```

---

## QThread Patterns

### Pattern 1: Subclass QThread (Simple Tasks)
```python
class SimpleWorkerThread(QThread):
    progress = Signal(int)
    result = Signal(object)
    error = Signal(str)
    
    def __init__(self, data, parent=None):
        super().__init__(parent)
        self._data = data
        self._cancelled = False
    
    def run(self):
        """Override run() ONLY. Do not override __init__ with heavy work."""
        try:
            for i, item in enumerate(self._data):
                if self._cancelled:
                    return
                # Do work
                processed = self.process_item(item)
                self.progress.emit(int((i + 1) / len(self._data) * 100))
            self.result.emit(processed)
        except Exception as e:
            self.error.emit(str(e))
    
    def process_item(self, item):
        # CPU-bound work here
        return item * 2
    
    @Slot()
    def cancel(self):
        self._cancelled = True

# Usage
thread = SimpleWorkerThread(data)
thread.progress.connect(progress_bar.setValue)
thread.result.connect(on_result)
thread.finished.connect(thread.deleteLater)
thread.start()
```

### Pattern 2: Worker Object (Recommended)
```python
class Worker(QObject):
    """Worker lives in a QThread, communicates via signals."""
    started = Signal()
    finished = Signal()
    progress = Signal(int)
    result = Signal(object)
    error = Signal(str)
    
    def __init__(self):
        super().__init__()  # NO PARENT - will be moved to thread
        self._cancelled = False
    
    @Slot()
    def run(self):
        self.started.emit()
        try:
            for i in range(100):
                if self._cancelled:
                    break
                QThread.msleep(50)  # Simulate work
                self.progress.emit(i + 1)
            self.result.emit({"status": "complete"})
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.finished.emit()
    
    @Slot()
    def cancel(self):
        self._cancelled = True


class Controller(QObject):
    """Manages worker lifecycle."""
    start_work = Signal()
    cancel_work = Signal()
    
    def __init__(self):
        super().__init__()
        self._thread = None
        self._worker = None
    
    def start(self):
        if self._thread is not None:
            return  # Already running
        
        self._thread = QThread()
        self._worker = Worker()
        self._worker.moveToThread(self._thread)
        
        # Connect signals
        self._thread.started.connect(self._worker.run)
        self.cancel_work.connect(self._worker.cancel)
        
        # Cleanup connections
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_thread_finished)
        
        # Result handling
        self._worker.result.connect(self._on_result)
        self._worker.error.connect(self._on_error)
        
        self._thread.start()
    
    def stop(self):
        if self._thread:
            self.cancel_work.emit()
            self._thread.quit()
            self._thread.wait(5000)  # Wait up to 5 seconds
    
    def _on_thread_finished(self):
        self._thread = None
        self._worker = None
    
    @Slot(object)
    def _on_result(self, data):
        print(f"Result: {data}")
    
    @Slot(str)
    def _on_error(self, msg):
        print(f"Error: {msg}")
```

---

## Worker Object Pattern

### Full Production Worker
```python
from dataclasses import dataclass
from typing import Optional
from PySide6.QtCore import QObject, Signal, Slot, QThread, QMutex, QMutexLocker

@dataclass
class WorkItem:
    id: int
    data: dict

@dataclass
class WorkResult:
    id: int
    success: bool
    result: Optional[dict] = None
    error: Optional[str] = None


class ProductionWorker(QObject):
    # Lifecycle signals
    started = Signal()
    finished = Signal()
    
    # Progress signals
    progress = Signal(int, int)  # current, total
    status = Signal(str)
    
    # Result signals
    item_completed = Signal(WorkResult)
    all_completed = Signal(list)  # List[WorkResult]
    error = Signal(str)
    
    def __init__(self):
        super().__init__()
        self._mutex = QMutex()
        self._cancelled = False
        self._paused = False
        self._items: list[WorkItem] = []
    
    @Slot(list)
    def set_items(self, items: list[WorkItem]):
        with QMutexLocker(self._mutex):
            self._items = items
    
    @Slot()
    def run(self):
        self.started.emit()
        results = []
        
        try:
            with QMutexLocker(self._mutex):
                items = list(self._items)
            
            total = len(items)
            for i, item in enumerate(items):
                # Check cancellation
                with QMutexLocker(self._mutex):
                    if self._cancelled:
                        self.status.emit("Cancelled")
                        break
                    while self._paused:
                        QThread.msleep(100)
                
                self.status.emit(f"Processing item {item.id}")
                
                try:
                    result_data = self._process_item(item)
                    result = WorkResult(item.id, True, result_data)
                except Exception as e:
                    result = WorkResult(item.id, False, error=str(e))
                
                results.append(result)
                self.item_completed.emit(result)
                self.progress.emit(i + 1, total)
            
            self.all_completed.emit(results)
            
        except Exception as e:
            self.error.emit(f"Fatal error: {e}")
        finally:
            self.finished.emit()
    
    def _process_item(self, item: WorkItem) -> dict:
        # Actual work here
        QThread.msleep(100)  # Simulate work
        return {"processed": item.data}
    
    @Slot()
    def cancel(self):
        with QMutexLocker(self._mutex):
            self._cancelled = True
    
    @Slot()
    def pause(self):
        with QMutexLocker(self._mutex):
            self._paused = True
    
    @Slot()
    def resume(self):
        with QMutexLocker(self._mutex):
            self._paused = False
```

---

## QtConcurrent

### Run Single Function
```python
from PySide6.QtConcurrent import QtConcurrent
from PySide6.QtCore import QFuture, QFutureWatcher

def expensive_computation(data: list) -> dict:
    # CPU-intensive work
    result = sum(x * x for x in data)
    return {"sum_squares": result}

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._watcher = QFutureWatcher(self)
        self._watcher.finished.connect(self._on_finished)
    
    def start_computation(self, data: list):
        future = QtConcurrent.run(expensive_computation, data)
        self._watcher.setFuture(future)
    
    @Slot()
    def _on_finished(self):
        result = self._watcher.result()
        print(f"Result: {result}")
```

### Map/Filter Operations
```python
from PySide6.QtConcurrent import QtConcurrent

def process_item(item: str) -> str:
    return item.upper()

def filter_item(item: str) -> bool:
    return len(item) > 3

# Map (transform each item)
future = QtConcurrent.mapped(items, process_item)
watcher.setFuture(future)

# Filter (select items)
future = QtConcurrent.filtered(items, filter_item)

# Map-Reduce
def reduce_func(accumulated: int, item: str) -> int:
    return accumulated + len(item)

future = QtConcurrent.mappedReduced(items, process_item, reduce_func)
```

---

## Thread Pools

### Global Thread Pool
```python
from PySide6.QtCore import QThreadPool, QRunnable, Slot

class Task(QRunnable):
    def __init__(self, task_id: int, callback):
        super().__init__()
        self.task_id = task_id
        self.callback = callback
        self.setAutoDelete(True)
    
    @Slot()
    def run(self):
        # Work done in thread pool thread
        result = self.task_id * 2
        # Cannot call callback directly (wrong thread)
        # Use QMetaObject or signal instead
        QMetaObject.invokeMethod(
            self.callback, "on_result",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(int, result)
        )

# Usage
pool = QThreadPool.globalInstance()
print(f"Max threads: {pool.maxThreadCount()}")

for i in range(100):
    task = Task(i, result_handler)
    pool.start(task)
```

### Custom Thread Pool
```python
class WorkerPool:
    def __init__(self, max_threads: int = 4):
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(max_threads)
    
    def submit(self, func, *args, **kwargs):
        task = FunctionRunnable(func, args, kwargs)
        self._pool.start(task)
    
    def wait_all(self):
        self._pool.waitForDone()
    
    def clear(self):
        self._pool.clear()


class FunctionRunnable(QRunnable):
    def __init__(self, func, args, kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
    
    def run(self):
        self.func(*self.args, **self.kwargs)
```

---

## Async Integration

### Qt 6.8+ Native Async (PySide6.QtAsyncio)
```python
import asyncio
from PySide6.QtAsyncio import QAsyncioEventLoopPolicy
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton
from PySide6.QtCore import Slot

# MUST set policy before creating QApplication
asyncio.set_event_loop_policy(QAsyncioEventLoopPolicy())

class AsyncMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.button = QPushButton("Fetch Data")
        self.button.clicked.connect(self.on_click)
        self.setCentralWidget(self.button)
    
    @Slot()
    def on_click(self):
        asyncio.ensure_future(self.fetch_data())
    
    async def fetch_data(self):
        self.button.setEnabled(False)
        self.button.setText("Loading...")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://api.example.com/data") as resp:
                    data = await resp.json()
            self.button.setText(f"Got {len(data)} items")
        except Exception as e:
            self.button.setText(f"Error: {e}")
        finally:
            self.button.setEnabled(True)

if __name__ == "__main__":
    app = QApplication([])
    window = AsyncMainWindow()
    window.show()
    
    # Run with asyncio event loop
    asyncio.get_event_loop().run_forever()
```

### Async with Cancellation
```python
class AsyncController:
    def __init__(self):
        self._current_task: Optional[asyncio.Task] = None
    
    def start_task(self):
        if self._current_task:
            self._current_task.cancel()
        self._current_task = asyncio.ensure_future(self._do_work())
    
    def cancel_task(self):
        if self._current_task:
            self._current_task.cancel()
    
    async def _do_work(self):
        try:
            for i in range(100):
                await asyncio.sleep(0.1)
                print(f"Progress: {i}%")
        except asyncio.CancelledError:
            print("Task cancelled")
            raise
```

---

## Thread-Safe Data Structures

### Thread-Safe Queue
```python
from PySide6.QtCore import QMutex, QMutexLocker, QWaitCondition
from collections import deque

class ThreadSafeQueue:
    def __init__(self, maxsize: int = 0):
        self._queue = deque()
        self._mutex = QMutex()
        self._not_empty = QWaitCondition()
        self._not_full = QWaitCondition()
        self._maxsize = maxsize
    
    def put(self, item, timeout_ms: int = -1) -> bool:
        with QMutexLocker(self._mutex):
            if self._maxsize > 0:
                while len(self._queue) >= self._maxsize:
                    if not self._not_full.wait(self._mutex, timeout_ms):
                        return False
            self._queue.append(item)
            self._not_empty.wakeOne()
            return True
    
    def get(self, timeout_ms: int = -1):
        with QMutexLocker(self._mutex):
            while not self._queue:
                if not self._not_empty.wait(self._mutex, timeout_ms):
                    return None
            item = self._queue.popleft()
            if self._maxsize > 0:
                self._not_full.wakeOne()
            return item
    
    def qsize(self) -> int:
        with QMutexLocker(self._mutex):
            return len(self._queue)
```

### Read-Write Lock Pattern
```python
from PySide6.QtCore import QReadWriteLock, QReadLocker, QWriteLocker

class ThreadSafeCache:
    def __init__(self):
        self._cache = {}
        self._lock = QReadWriteLock()
    
    def get(self, key):
        with QReadLocker(self._lock):
            return self._cache.get(key)
    
    def set(self, key, value):
        with QWriteLocker(self._lock):
            self._cache[key] = value
    
    def get_all(self) -> dict:
        with QReadLocker(self._lock):
            return dict(self._cache)
    
    def clear(self):
        with QWriteLocker(self._lock):
            self._cache.clear()
```

---

## Common Mistakes

### ❌ GUI Operations from Worker Thread
```python
# WRONG: Crash or undefined behavior
class BadWorker(QThread):
    def run(self):
        for i in range(100):
            self.progress_bar.setValue(i)  # THREAD VIOLATION!
            self.label.setText(f"{i}%")    # THREAD VIOLATION!

# CORRECT: Use signals
class GoodWorker(QThread):
    progress = Signal(int)
    status = Signal(str)
    
    def run(self):
        for i in range(100):
            self.progress.emit(i)
            self.status.emit(f"{i}%")
```

### ❌ Creating QObjects in run()
```python
# WRONG: QTimer created in wrong thread
class BadWorker(QThread):
    def run(self):
        timer = QTimer()  # Created in worker thread
        timer.timeout.connect(self.tick)
        timer.start(1000)
        self.exec()  # Timer won't work as expected

# CORRECT: Create in __init__ or use moveToThread pattern
```

### ❌ Blocking Event Loop
```python
# WRONG: Freezes UI
def on_button_click(self):
    time.sleep(5)                    # BLOCKS
    result = requests.get(url)       # BLOCKS
    data = heavy_computation()       # BLOCKS

# CORRECT: Use worker thread
def on_button_click(self):
    self.worker = Worker()
    self.worker.moveToThread(self.thread)
    self.thread.started.connect(self.worker.run)
    self.thread.start()
```

### ❌ Missing moveToThread() Parent Check
```python
# WRONG: Object with parent cannot be moved
worker = Worker(parent=self)  # Has parent!
worker.moveToThread(thread)   # ERROR or crash

# CORRECT: No parent for objects that will be moved
worker = Worker()  # No parent
worker.moveToThread(thread)
```

### ❌ Using QPixmap in Threads
```python
# WRONG: QPixmap is not thread-safe
class BadImageWorker(QThread):
    def run(self):
        pixmap = QPixmap("image.png")  # NOT THREAD SAFE!
        scaled = pixmap.scaled(100, 100)

# CORRECT: Use QImage in threads
class GoodImageWorker(QThread):
    result = Signal(QImage)
    
    def run(self):
        image = QImage("image.png")  # Thread-safe
        scaled = image.scaled(100, 100)
        self.result.emit(scaled)

# In main thread, convert to QPixmap if needed
@Slot(QImage)
def on_image_ready(self, image):
    pixmap = QPixmap.fromImage(image)
    self.label.setPixmap(pixmap)
```

### ❌ Race Condition in Cancellation
```python
# WRONG: Race condition
class BadWorker(QThread):
    def __init__(self):
        self._cancelled = False
    
    def run(self):
        while not self._cancelled:  # Not atomic!
            do_work()
    
    def cancel(self):
        self._cancelled = True  # Race!

# CORRECT: Use mutex or atomic
class GoodWorker(QThread):
    def __init__(self):
        self._mutex = QMutex()
        self._cancelled = False
    
    def run(self):
        while True:
            with QMutexLocker(self._mutex):
                if self._cancelled:
                    break
            do_work()
    
    @Slot()
    def cancel(self):
        with QMutexLocker(self._mutex):
            self._cancelled = True
```
