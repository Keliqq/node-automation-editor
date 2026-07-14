# Widget Patterns Reference

## Table of Contents
1. [Widget Lifecycle](#widget-lifecycle)
2. [Layout Management](#layout-management)
3. [Custom Widgets](#custom-widgets)
4. [Styling (QSS)](#styling)
5. [High-DPI Support](#high-dpi-support)
6. [Event Handling](#event-handling)
7. [Common Mistakes](#common-mistakes)

---

## Widget Lifecycle

### Creation and Ownership
```python
from PySide6.QtWidgets import QWidget, QLabel, QPushButton, QVBoxLayout

class MyWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Option 1: Parent in constructor (automatic cleanup)
        self.label = QLabel("Text", self)
        
        # Option 2: Add to layout (layout takes ownership)
        self.button = QPushButton("Click")  # No parent
        layout = QVBoxLayout(self)
        layout.addWidget(self.button)  # Layout parents it
        
        # WRONG: No parent, no layout = memory leak
        orphan = QLabel("Leaked!")  # Memory leak!
```

### Deletion Patterns
```python
# Safe deletion of QObjects
widget.deleteLater()  # Queued deletion, safe even with pending signals

# WRONG: Direct deletion
del widget            # May crash if signals pending
widget = None         # Same problem

# Hide vs Delete
widget.hide()         # Widget still exists, can be shown again
widget.deleteLater()  # Widget will be destroyed

# Conditional cleanup
def cleanup_widget(self):
    if self.temp_widget is not None:
        self.temp_widget.deleteLater()
        self.temp_widget = None
```

### Init Order
```python
class ProperWidget(QWidget):
    def __init__(self, data: dict, parent=None):
        # 1. ALWAYS call super().__init__ first
        super().__init__(parent)
        
        # 2. Store data
        self._data = data
        
        # 3. Set window properties
        self.setWindowTitle("Title")
        self.setMinimumSize(400, 300)
        
        # 4. Create child widgets
        self._create_widgets()
        
        # 5. Create layout
        self._create_layout()
        
        # 6. Connect signals
        self._connect_signals()
        
        # 7. Initial state
        self._load_data()
```

---

## Layout Management

### Common Layouts
```python
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout, QStackedLayout
)

# Vertical layout
vbox = QVBoxLayout()
vbox.addWidget(widget1)
vbox.addWidget(widget2)
vbox.addStretch()  # Pushes widgets up

# Horizontal layout
hbox = QHBoxLayout()
hbox.addWidget(left)
hbox.addStretch()  # Space between
hbox.addWidget(right)

# Grid layout
grid = QGridLayout()
grid.addWidget(widget, row, col)
grid.addWidget(widget, row, col, rowSpan, colSpan)

# Form layout (label-field pairs)
form = QFormLayout()
form.addRow("Name:", name_edit)
form.addRow("Email:", email_edit)
form.addRow(submit_button)  # Spans both columns
```

### Layout Properties
```python
layout = QVBoxLayout()

# Margins (left, top, right, bottom)
layout.setContentsMargins(10, 10, 10, 10)

# Spacing between items
layout.setSpacing(5)

# Alignment
layout.setAlignment(Qt.AlignmentFlag.AlignTop)

# Size policies
widget.setSizePolicy(
    QSizePolicy.Policy.Expanding,   # Horizontal
    QSizePolicy.Policy.Fixed        # Vertical
)

# Stretch factors
layout.addWidget(small, stretch=1)
layout.addWidget(big, stretch=3)  # 3x the space
```

### Nested Layouts
```python
class ComplexWidget(QWidget):
    def __init__(self):
        super().__init__()
        
        # Main vertical layout
        main_layout = QVBoxLayout(self)
        
        # Header (horizontal)
        header = QHBoxLayout()
        header.addWidget(QLabel("Title"))
        header.addStretch()
        header.addWidget(QPushButton("Settings"))
        main_layout.addLayout(header)
        
        # Content (grid)
        content = QGridLayout()
        content.addWidget(QLabel("Name:"), 0, 0)
        content.addWidget(QLineEdit(), 0, 1)
        main_layout.addLayout(content)
        
        # Footer
        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(QPushButton("Cancel"))
        footer.addWidget(QPushButton("OK"))
        main_layout.addLayout(footer)
```

---

## Custom Widgets

### Minimal Custom Widget
```python
class CustomWidget(QWidget):
    value_changed = Signal(int)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0
        self.setMinimumSize(100, 50)
    
    @property
    def value(self):
        return self._value
    
    @value.setter
    def value(self, v: int):
        if self._value != v:
            self._value = v
            self.update()  # Request repaint
            self.value_changed.emit(v)
    
    def sizeHint(self):
        return QSize(200, 100)
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Draw background
        painter.fillRect(self.rect(), Qt.GlobalColor.white)
        
        # Draw content
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, str(self._value))
```

### Interactive Custom Widget
```python
class ClickableWidget(QWidget):
    clicked = Signal()
    double_clicked = Signal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._hovered = False
        self._pressed = False
        self.setMouseTracking(True)  # Receive move events without button
        self.setCursor(Qt.CursorShape.PointingHandCursor)
    
    def enterEvent(self, event):
        self._hovered = True
        self.update()
    
    def leaveEvent(self, event):
        self._hovered = False
        self._pressed = False
        self.update()
    
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()
    
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._pressed:
            self._pressed = False
            if self.rect().contains(event.position().toPoint()):
                self.clicked.emit()
            self.update()
    
    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
    
    def paintEvent(self, event):
        painter = QPainter(self)
        
        # State-dependent appearance
        if self._pressed:
            color = Qt.GlobalColor.darkGray
        elif self._hovered:
            color = Qt.GlobalColor.lightGray
        else:
            color = Qt.GlobalColor.white
        
        painter.fillRect(self.rect(), color)
```

---

## Styling

### Qt Style Sheets (QSS)
```python
# Set on single widget
button.setStyleSheet("""
    QPushButton {
        background-color: #4CAF50;
        color: white;
        border: none;
        padding: 8px 16px;
        border-radius: 4px;
    }
    QPushButton:hover {
        background-color: #45a049;
    }
    QPushButton:pressed {
        background-color: #3d8b40;
    }
    QPushButton:disabled {
        background-color: #cccccc;
        color: #666666;
    }
""")

# Set on application (global)
app.setStyleSheet("""
    QWidget {
        font-family: "Segoe UI", Arial, sans-serif;
        font-size: 12px;
    }
    QLineEdit, QTextEdit, QComboBox {
        border: 1px solid #ccc;
        border-radius: 4px;
        padding: 4px;
    }
    QLineEdit:focus, QTextEdit:focus {
        border-color: #4CAF50;
    }
""")
```

### Dynamic Styling
```python
class ThemedWidget(QWidget):
    def __init__(self):
        super().__init__()
        self._theme = "light"
    
    def set_theme(self, theme: str):
        self._theme = theme
        self._apply_theme()
    
    def _apply_theme(self):
        if self._theme == "dark":
            self.setStyleSheet("""
                QWidget { background-color: #2b2b2b; color: #ffffff; }
                QLineEdit { background-color: #3c3c3c; border: 1px solid #555; }
            """)
        else:
            self.setStyleSheet("""
                QWidget { background-color: #ffffff; color: #000000; }
                QLineEdit { background-color: #ffffff; border: 1px solid #ccc; }
            """)
```

### Style Properties (Custom)
```python
class StyledButton(QPushButton):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        # Set custom property for QSS targeting
        self.setProperty("class", "primary")
    
    def set_style_class(self, cls: str):
        self.setProperty("class", cls)
        # Force style recalculation
        self.style().unpolish(self)
        self.style().polish(self)

# QSS targeting custom property
"""
QPushButton[class="primary"] {
    background-color: #007bff;
    color: white;
}
QPushButton[class="danger"] {
    background-color: #dc3545;
    color: white;
}
"""
```

---

## High-DPI Support

### Qt 6.8+ High-DPI (Automatic)
```python
# Qt 6 enables high-DPI by default
# These are the defaults, no need to set:
# QApplication.setHighDpiScaleFactorRoundingPolicy(
#     Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
# )

# For custom painting, use device-independent sizes
class HighDpiWidget(QWidget):
    def paintEvent(self, event):
        painter = QPainter(self)
        
        # Use logical coordinates (device-independent)
        # Qt handles scaling automatically
        painter.drawRect(10, 10, 100, 100)  # Logical pixels
        
        # For pixel-perfect work, query device pixel ratio
        dpr = self.devicePixelRatio()
        physical_width = self.width() * dpr
```

### Icon Handling
```python
from PySide6.QtGui import QIcon

# Let Qt choose appropriate resolution
icon = QIcon(":/icons/app.png")  # Qt selects @2x variant automatically

# Or provide multiple resolutions
icon = QIcon()
icon.addFile(":/icons/app.png", QSize(16, 16))
icon.addFile(":/icons/app@2x.png", QSize(32, 32))

# For SVG (scales perfectly)
icon = QIcon(":/icons/app.svg")  # Best for high-DPI
```

### Font Sizing
```python
# Use point sizes (scale correctly)
font = QFont("Arial", 12)  # 12pt, scales with DPI

# Avoid pixel sizes
font.setPixelSize(16)  # WRONG: Won't scale

# Application-wide default
app.setFont(QFont("Segoe UI", 10))
```

---

## Event Handling

### Event Filter Pattern
```python
class EventFilterExample(QWidget):
    def __init__(self):
        super().__init__()
        self.line_edit = QLineEdit()
        self.line_edit.installEventFilter(self)
    
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj == self.line_edit:
            if event.type() == QEvent.Type.KeyPress:
                if event.key() == Qt.Key.Key_Return:
                    self.handle_submit()
                    return True  # Consume event
            elif event.type() == QEvent.Type.FocusIn:
                self.line_edit.selectAll()
        
        return super().eventFilter(obj, event)  # Pass to default handler
```

### Custom Events
```python
from PySide6.QtCore import QEvent

class DataUpdatedEvent(QEvent):
    EventType = QEvent.Type(QEvent.registerEventType())
    
    def __init__(self, data: dict):
        super().__init__(DataUpdatedEvent.EventType)
        self.data = data

class DataWidget(QWidget):
    def customEvent(self, event: QEvent):
        if event.type() == DataUpdatedEvent.EventType:
            self.handle_data_update(event.data)
        else:
            super().customEvent(event)

# Post event from anywhere
QCoreApplication.postEvent(widget, DataUpdatedEvent({"key": "value"}))
```

---

## Common Mistakes

### ❌ Modifying Layout During Iteration
```python
# WRONG: Modifying while iterating
for i in range(layout.count()):
    widget = layout.itemAt(i).widget()
    if should_remove(widget):
        layout.removeWidget(widget)  # Invalidates indices!

# CORRECT: Iterate in reverse or collect first
for i in reversed(range(layout.count())):
    widget = layout.itemAt(i).widget()
    if should_remove(widget):
        layout.removeWidget(widget)
        widget.deleteLater()
```

### ❌ Creating Widgets in paintEvent
```python
# WRONG: Creates new objects every paint
def paintEvent(self, event):
    font = QFont("Arial", 12)      # Created every paint!
    pen = QPen(Qt.black, 2)        # Created every paint!
    painter = QPainter(self)
    painter.setFont(font)

# CORRECT: Reuse objects
def __init__(self):
    self._font = QFont("Arial", 12)
    self._pen = QPen(Qt.GlobalColor.black, 2)

def paintEvent(self, event):
    painter = QPainter(self)
    painter.setFont(self._font)
    painter.setPen(self._pen)
```

### ❌ Using repaint() Instead of update()
```python
# WRONG: Forces immediate repaint
self.repaint()  # Blocks, can cause flicker

# CORRECT: Request repaint (Qt optimizes)
self.update()   # Queued, coalesced
self.update(rect)  # Partial update for efficiency
```

### ❌ Missing Parent or Layout
```python
# WRONG: Memory leak
def create_widgets(self):
    label = QLabel("Text")  # Orphaned!
    self.labels.append(label)

# CORRECT: Ensure ownership
def create_widgets(self):
    label = QLabel("Text", self)  # Parent ownership
    # OR
    label = QLabel("Text")
    self.layout().addWidget(label)  # Layout ownership
```

### ❌ Widget Size Before show()
```python
# WRONG: Sizes not finalized before show
def __init__(self):
    super().__init__()
    print(self.width())  # Returns 100 (minimum), not actual

# CORRECT: Query after show or use events
def showEvent(self, event):
    super().showEvent(event)
    print(self.width())  # Actual size

# Or use QTimer
def __init__(self):
    QTimer.singleShot(0, self.on_shown)
```
