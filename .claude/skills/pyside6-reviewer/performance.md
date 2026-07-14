# Performance Optimization Reference

## Table of Contents
1. [Painting Performance](#painting-performance)
2. [Model Performance](#model-performance)
3. [Memory Optimization](#memory-optimization)
4. [Event Loop Health](#event-loop-health)
5. [Lazy Loading](#lazy-loading)
6. [Profiling Tools](#profiling-tools)
7. [Common Performance Mistakes](#common-performance-mistakes)

---

## Painting Performance

### Minimize Paint Operations
```python
class OptimizedWidget(QWidget):
    def __init__(self):
        super().__init__()
        # Pre-create resources ONCE
        self._pen = QPen(Qt.GlobalColor.black, 2)
        self._brush = QBrush(Qt.GlobalColor.blue)
        self._font = QFont("Arial", 12)
        self._cached_path = QPainterPath()
        self._dirty = True
    
    def set_data(self, data):
        self._data = data
        self._dirty = True
        self.update()  # Request repaint, NOT repaint()
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Only recalculate path when data changes
        if self._dirty:
            self._rebuild_path()
            self._dirty = False
        
        # Reuse cached resources
        painter.setPen(self._pen)
        painter.setBrush(self._brush)
        painter.setFont(self._font)
        painter.drawPath(self._cached_path)
    
    def _rebuild_path(self):
        self._cached_path = QPainterPath()
        # Build complex path once
        for item in self._data:
            self._cached_path.addRect(item.rect)
```

### Partial Updates
```python
class EfficientCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self._items = []
        self._dirty_regions = []
    
    def update_item(self, item):
        """Only repaint the affected region."""
        old_rect = item.rect
        item.update()
        new_rect = item.rect
        
        # Request update for affected area only
        self.update(old_rect.united(new_rect))
    
    def paintEvent(self, event):
        painter = QPainter(self)
        
        # Only paint items that intersect dirty region
        dirty_rect = event.rect()
        for item in self._items:
            if item.rect.intersects(dirty_rect):
                item.paint(painter)
```

### Double Buffering (Complex Graphics)
```python
class BufferedWidget(QWidget):
    def __init__(self):
        super().__init__()
        self._buffer = None
        self._buffer_dirty = True
    
    def resizeEvent(self, event):
        # Recreate buffer on resize
        self._buffer = QPixmap(self.size())
        self._buffer_dirty = True
        super().resizeEvent(event)
    
    def invalidate(self):
        self._buffer_dirty = True
        self.update()
    
    def paintEvent(self, event):
        if self._buffer is None:
            self._buffer = QPixmap(self.size())
            self._buffer_dirty = True
        
        if self._buffer_dirty:
            # Render to buffer
            self._buffer.fill(Qt.GlobalColor.transparent)
            buffer_painter = QPainter(self._buffer)
            self._render_content(buffer_painter)
            buffer_painter.end()
            self._buffer_dirty = False
        
        # Blit buffer to screen
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._buffer)
    
    def _render_content(self, painter):
        # Expensive drawing operations
        pass
```

---

## Model Performance

### Efficient Data Updates
```python
class EfficientModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self._items = []
    
    def set_items(self, items: list):
        """Efficient bulk replacement."""
        self.beginResetModel()
        self._items = items
        self.endResetModel()
    
    def batch_update(self, updates: dict[int, dict]):
        """Update multiple items with single signal."""
        if not updates:
            return
        
        for row, data in updates.items():
            if 0 <= row < len(self._items):
                self._items[row].update(data)
        
        # Single dataChanged signal for range
        rows = list(updates.keys())
        top_left = self.index(min(rows))
        bottom_right = self.index(max(rows))
        self.dataChanged.emit(top_left, bottom_right, [])
    
    def append_items(self, items: list):
        """Efficient bulk append."""
        if not items:
            return
        start = len(self._items)
        end = start + len(items) - 1
        
        self.beginInsertRows(QModelIndex(), start, end)
        self._items.extend(items)
        self.endInsertRows()
```

### Virtual Models (Large Datasets)
```python
class VirtualModel(QAbstractListModel):
    """Fetches data on-demand for million+ items."""
    
    def __init__(self, data_source):
        super().__init__()
        self._source = data_source
        self._cache = {}
        self._cache_size = 1000
        self._total_count = data_source.count()
    
    def rowCount(self, parent=QModelIndex()):
        return self._total_count
    
    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        
        row = index.row()
        
        # Check cache first
        if row not in self._cache:
            self._fetch_page(row)
        
        item = self._cache.get(row)
        if item is None:
            return None
        
        if role == Qt.ItemDataRole.DisplayRole:
            return item.name
        return None
    
    def _fetch_page(self, row: int, page_size: int = 100):
        """Fetch page of items around requested row."""
        start = max(0, row - page_size // 2)
        end = min(self._total_count, start + page_size)
        
        items = self._source.fetch(start, end)
        
        # Manage cache size
        if len(self._cache) > self._cache_size:
            # Remove items far from current view
            keys_to_remove = [
                k for k in self._cache.keys()
                if abs(k - row) > page_size * 2
            ][:len(self._cache) - self._cache_size // 2]
            for k in keys_to_remove:
                del self._cache[k]
        
        # Add new items to cache
        for i, item in enumerate(items, start):
            self._cache[i] = item
```

### Deferred Model Updates
```python
class DeferredModel(QAbstractListModel):
    """Batches rapid updates to prevent UI stuttering."""
    
    def __init__(self):
        super().__init__()
        self._items = []
        self._pending_changes = []
        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.setInterval(50)  # 50ms debounce
        self._update_timer.timeout.connect(self._apply_changes)
    
    def add_item(self, item):
        self._pending_changes.append(('add', item))
        self._update_timer.start()
    
    def _apply_changes(self):
        if not self._pending_changes:
            return
        
        # Group by operation type
        adds = [c[1] for c in self._pending_changes if c[0] == 'add']
        
        if adds:
            start = len(self._items)
            end = start + len(adds) - 1
            self.beginInsertRows(QModelIndex(), start, end)
            self._items.extend(adds)
            self.endInsertRows()
        
        self._pending_changes.clear()
```

---

## Memory Optimization

### Widget Pooling
```python
class WidgetPool:
    """Reuse expensive widgets instead of recreating."""
    
    def __init__(self, factory, max_size: int = 50):
        self._factory = factory
        self._pool = []
        self._max_size = max_size
    
    def acquire(self) -> QWidget:
        if self._pool:
            widget = self._pool.pop()
            widget.show()
            return widget
        return self._factory()
    
    def release(self, widget: QWidget):
        widget.hide()
        if len(self._pool) < self._max_size:
            self._pool.append(widget)
        else:
            widget.deleteLater()
    
    def clear(self):
        for widget in self._pool:
            widget.deleteLater()
        self._pool.clear()
```

### Image Caching
```python
class ImageCache:
    """LRU cache for images."""
    
    def __init__(self, max_size_mb: int = 100):
        self._cache = {}
        self._access_order = []
        self._max_bytes = max_size_mb * 1024 * 1024
        self._current_bytes = 0
    
    def get(self, path: str) -> QPixmap:
        if path in self._cache:
            # Move to end (most recent)
            self._access_order.remove(path)
            self._access_order.append(path)
            return self._cache[path]
        
        # Load and cache
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return pixmap
        
        size = self._estimate_size(pixmap)
        
        # Evict if necessary
        while self._current_bytes + size > self._max_bytes and self._access_order:
            oldest = self._access_order.pop(0)
            evicted = self._cache.pop(oldest)
            self._current_bytes -= self._estimate_size(evicted)
        
        self._cache[path] = pixmap
        self._access_order.append(path)
        self._current_bytes += size
        
        return pixmap
    
    def _estimate_size(self, pixmap: QPixmap) -> int:
        return pixmap.width() * pixmap.height() * pixmap.depth() // 8
```

---

## Event Loop Health

### Avoid Blocking
```python
class ResponsiveProcessor:
    """Process large datasets without blocking UI."""
    
    def __init__(self, parent):
        self._timer = QTimer(parent)
        self._timer.timeout.connect(self._process_chunk)
        self._items = []
        self._index = 0
        self._chunk_size = 100
    
    def start(self, items: list):
        self._items = items
        self._index = 0
        self._timer.start(0)  # Process in idle time
    
    def _process_chunk(self):
        end = min(self._index + self._chunk_size, len(self._items))
        
        for i in range(self._index, end):
            self._process_item(self._items[i])
        
        self._index = end
        
        if self._index >= len(self._items):
            self._timer.stop()
            self._on_complete()
    
    def _process_item(self, item):
        # Process single item
        pass
    
    def _on_complete(self):
        pass
```

### processEvents Sparingly
```python
# AVOID when possible - prefer chunked processing or threads
def long_operation(self):
    for i, item in enumerate(items):
        process(item)
        if i % 100 == 0:
            QApplication.processEvents()  # Keep UI responsive
            if self._cancelled:
                break

# BETTER: Use worker thread or chunked timer
```

---

## Lazy Loading

### Lazy Widget Creation
```python
class LazyTabWidget(QTabWidget):
    """Create tab contents only when first viewed."""
    
    def __init__(self):
        super().__init__()
        self._factories = {}
        self._created = set()
        self.currentChanged.connect(self._on_tab_changed)
    
    def add_lazy_tab(self, factory, title: str, icon=None):
        """Add tab with factory function instead of widget."""
        placeholder = QWidget()
        index = self.addTab(placeholder, title)
        if icon:
            self.setTabIcon(index, icon)
        self._factories[index] = factory
    
    def _on_tab_changed(self, index: int):
        if index in self._factories and index not in self._created:
            factory = self._factories[index]
            widget = factory()
            self.removeTab(index)
            self.insertTab(index, widget, self.tabText(index))
            self.setCurrentIndex(index)
            self._created.add(index)
```

### Lazy Data Loading
```python
class LazyListView(QListView):
    """Load visible items only."""
    
    load_more = Signal(int, int)  # start, count
    
    def __init__(self):
        super().__init__()
        self.verticalScrollBar().valueChanged.connect(self._check_scroll)
        self._loading = False
        self._buffer = 50  # Items to preload
    
    def _check_scroll(self):
        if self._loading:
            return
        
        scrollbar = self.verticalScrollBar()
        if scrollbar.value() > scrollbar.maximum() - 100:
            self._request_more()
    
    def _request_more(self):
        model = self.model()
        if model:
            current_count = model.rowCount()
            self._loading = True
            self.load_more.emit(current_count, self._buffer)
    
    def on_data_loaded(self):
        self._loading = False
```

---

## Profiling Tools

### Built-in Timing
```python
from PySide6.QtCore import QElapsedTimer

def measure_operation():
    timer = QElapsedTimer()
    timer.start()
    
    # Operation to measure
    expensive_operation()
    
    elapsed_ms = timer.elapsed()
    print(f"Operation took {elapsed_ms}ms")
```

### Custom Performance Monitor
```python
class PerformanceMonitor(QObject):
    """Track frame rate and operation times."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._frame_times = []
        self._max_samples = 60
        self._timer = QElapsedTimer()
        self._timer.start()
        self._last_frame = 0
    
    def frame_tick(self):
        """Call at end of each paint cycle."""
        now = self._timer.elapsed()
        frame_time = now - self._last_frame
        self._last_frame = now
        
        self._frame_times.append(frame_time)
        if len(self._frame_times) > self._max_samples:
            self._frame_times.pop(0)
    
    @property
    def fps(self) -> float:
        if not self._frame_times:
            return 0
        avg_ms = sum(self._frame_times) / len(self._frame_times)
        return 1000 / avg_ms if avg_ms > 0 else 0
    
    @property
    def avg_frame_time(self) -> float:
        if not self._frame_times:
            return 0
        return sum(self._frame_times) / len(self._frame_times)
```

---

## Common Performance Mistakes

### ❌ Creating Objects in Paint
```python
# WRONG: Creates garbage every paint
def paintEvent(self, event):
    painter = QPainter(self)
    font = QFont("Arial", 12)      # New font each paint
    pen = QPen(Qt.black)            # New pen each paint
    brush = QBrush(Qt.blue)         # New brush each paint

# CORRECT: Reuse objects
def __init__(self):
    self._font = QFont("Arial", 12)
    self._pen = QPen(Qt.GlobalColor.black)
    self._brush = QBrush(Qt.GlobalColor.blue)
```

### ❌ Unnecessary Full Repaints
```python
# WRONG: Full repaint for tiny change
def update_cursor(self, pos):
    self._cursor_pos = pos
    self.update()  # Repaints entire widget

# CORRECT: Repaint only affected region
def update_cursor(self, pos):
    old_rect = QRect(self._cursor_pos - QPoint(5, 5), QSize(10, 10))
    self._cursor_pos = pos
    new_rect = QRect(pos - QPoint(5, 5), QSize(10, 10))
    self.update(old_rect.united(new_rect))
```

### ❌ Signal Spam
```python
# WRONG: Emits signal for every item
def load_items(self, items):
    for item in items:
        self._items.append(item)
        self.item_added.emit(item)  # 1000 signals!

# CORRECT: Batch notification
def load_items(self, items):
    self.beginInsertRows(QModelIndex(), len(self._items), 
                         len(self._items) + len(items) - 1)
    self._items.extend(items)
    self.endInsertRows()  # Single notification
```

### ❌ Blocking Main Thread
```python
# WRONG: Freezes UI
def on_button_click(self):
    data = requests.get(url).json()  # Blocks!
    self.process_data(data)

# CORRECT: Use worker thread
def on_button_click(self):
    worker = Worker(url)
    worker.moveToThread(self._thread)
    worker.result.connect(self.process_data)
    self._thread.start()
```

### ❌ Deep Widget Hierarchies
```python
# WRONG: Deep nesting impacts layout performance
container
└── frame
    └── group
        └── scroll
            └── container
                └── widget  # 5 levels deep!

# CORRECT: Flatten when possible
container
└── scroll
    └── widget  # 2 levels
```
