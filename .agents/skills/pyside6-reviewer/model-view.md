# Model/View Architecture Reference

## Table of Contents
1. [Model Basics](#model-basics)
2. [Custom Model Implementation](#custom-model-implementation)
3. [Roles and Data Access](#roles-and-data-access)
4. [Model Modification Patterns](#model-modification-patterns)
5. [Proxy Models](#proxy-models)
6. [Delegates](#delegates)
7. [Common Mistakes](#common-mistakes)

---

## Model Basics

### Model Hierarchy
```
QAbstractItemModel (base, tree structure)
├── QAbstractListModel (flat list)
├── QAbstractTableModel (rows + columns)
├── QStandardItemModel (generic, NOT recommended for large data)
├── QStringListModel (simple string list)
└── QSqlTableModel / QSqlQueryModel (database)
```

### Choosing the Right Base
| Use Case | Base Class |
|----------|------------|
| Flat list, custom data | `QAbstractListModel` |
| Table, custom data | `QAbstractTableModel` |
| Tree hierarchy | `QAbstractItemModel` |
| Quick prototype | `QStandardItemModel` |
| < 1000 simple items | `QStringListModel` |
| Database table | `QSqlTableModel` |

---

## Custom Model Implementation

### Minimal List Model
```python
from PySide6.QtCore import QAbstractListModel, Qt, QModelIndex, Slot

class SimpleListModel(QAbstractListModel):
    def __init__(self, items: list = None, parent=None):
        super().__init__(parent)
        self._items = items or []
    
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        # List models must return 0 for valid parent
        if parent.isValid():
            return 0
        return len(self._items)
    
    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if not 0 <= index.row() < len(self._items):
            return None
        
        item = self._items[index.row()]
        
        if role == Qt.ItemDataRole.DisplayRole:
            return str(item)
        elif role == Qt.ItemDataRole.EditRole:
            return item
        return None
```

### Minimal Table Model
```python
class SimpleTableModel(QAbstractTableModel):
    def __init__(self, data: list[list] = None, headers: list[str] = None, parent=None):
        super().__init__(parent)
        self._data = data or []
        self._headers = headers or []
    
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._data)
    
    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._headers) if self._headers else (len(self._data[0]) if self._data else 0)
    
    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        
        row, col = index.row(), index.column()
        if not (0 <= row < len(self._data) and 0 <= col < len(self._data[row])):
            return None
        
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return self._data[row][col]
        return None
    
    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and section < len(self._headers):
            return self._headers[section]
        if orientation == Qt.Orientation.Vertical:
            return str(section + 1)
        return None
```

### Tree Model (Hierarchical)
```python
class TreeItem:
    def __init__(self, data: list, parent: 'TreeItem' = None):
        self._data = data
        self._parent = parent
        self._children = []
    
    def appendChild(self, item: 'TreeItem'):
        self._children.append(item)
    
    def child(self, row: int) -> 'TreeItem':
        return self._children[row] if 0 <= row < len(self._children) else None
    
    def childCount(self) -> int:
        return len(self._children)
    
    def columnCount(self) -> int:
        return len(self._data)
    
    def data(self, column: int):
        return self._data[column] if 0 <= column < len(self._data) else None
    
    def parent(self) -> 'TreeItem':
        return self._parent
    
    def row(self) -> int:
        if self._parent:
            return self._parent._children.index(self)
        return 0


class TreeModel(QAbstractItemModel):
    def __init__(self, root_data: list, parent=None):
        super().__init__(parent)
        self._root = TreeItem(root_data)
    
    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        
        parent_item = parent.internalPointer() if parent.isValid() else self._root
        child_item = parent_item.child(row)
        
        if child_item:
            return self.createIndex(row, column, child_item)
        return QModelIndex()
    
    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()
        
        child_item = index.internalPointer()
        parent_item = child_item.parent()
        
        if parent_item == self._root or parent_item is None:
            return QModelIndex()
        
        return self.createIndex(parent_item.row(), 0, parent_item)
    
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.column() > 0:
            return 0
        parent_item = parent.internalPointer() if parent.isValid() else self._root
        return parent_item.childCount()
    
    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return self._root.columnCount()
    
    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        item = index.internalPointer()
        return item.data(index.column())
```

---

## Roles and Data Access

### Standard Roles (Qt.ItemDataRole)
```python
# Display and Edit
Qt.ItemDataRole.DisplayRole      # Text displayed in view
Qt.ItemDataRole.EditRole         # Data for editing
Qt.ItemDataRole.DecorationRole   # Icon/image decoration

# Appearance
Qt.ItemDataRole.FontRole         # QFont for item
Qt.ItemDataRole.TextAlignmentRole  # Qt.AlignmentFlag
Qt.ItemDataRole.BackgroundRole   # QBrush for background
Qt.ItemDataRole.ForegroundRole   # QBrush for text color
Qt.ItemDataRole.CheckStateRole   # Qt.CheckState for checkboxes

# Tooltips and Status
Qt.ItemDataRole.ToolTipRole      # Tooltip text
Qt.ItemDataRole.StatusTipRole    # Status bar text
Qt.ItemDataRole.WhatsThisRole    # What's This help

# Internal
Qt.ItemDataRole.SizeHintRole     # QSize for preferred size
Qt.ItemDataRole.UserRole         # Base for custom roles
```

### Custom Roles
```python
class Roles:
    """Custom roles starting from UserRole."""
    IdRole = Qt.ItemDataRole.UserRole + 1
    TimestampRole = Qt.ItemDataRole.UserRole + 2
    StatusRole = Qt.ItemDataRole.UserRole + 3
    DataRole = Qt.ItemDataRole.UserRole + 4

class MyModel(QAbstractListModel):
    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        
        item = self._items[index.row()]
        
        match role:
            case Qt.ItemDataRole.DisplayRole:
                return item.name
            case Qt.ItemDataRole.DecorationRole:
                return item.icon
            case Roles.IdRole:
                return item.id
            case Roles.TimestampRole:
                return item.created_at
            case Roles.StatusRole:
                return item.status
            case Roles.DataRole:
                return item  # Return full object
        return None
    
    # Required for QML
    def roleNames(self) -> dict:
        return {
            Qt.ItemDataRole.DisplayRole: b"display",
            Qt.ItemDataRole.DecorationRole: b"decoration",
            Roles.IdRole: b"id",
            Roles.TimestampRole: b"timestamp",
            Roles.StatusRole: b"status",
            Roles.DataRole: b"modelData",
        }
```

---

## Model Modification Patterns

### Adding Items
```python
def addItem(self, item):
    """Insert item at end."""
    row = len(self._items)
    self.beginInsertRows(QModelIndex(), row, row)
    self._items.append(item)
    self.endInsertRows()

def insertItems(self, row: int, items: list):
    """Insert multiple items at position."""
    if not items:
        return
    self.beginInsertRows(QModelIndex(), row, row + len(items) - 1)
    self._items[row:row] = items
    self.endInsertRows()
```

### Removing Items
```python
def removeItem(self, row: int) -> bool:
    if not 0 <= row < len(self._items):
        return False
    self.beginRemoveRows(QModelIndex(), row, row)
    del self._items[row]
    self.endRemoveRows()
    return True

def removeItems(self, row: int, count: int) -> bool:
    if row < 0 or row + count > len(self._items):
        return False
    self.beginRemoveRows(QModelIndex(), row, row + count - 1)
    del self._items[row:row + count]
    self.endRemoveRows()
    return True

def clear(self):
    if not self._items:
        return
    self.beginResetModel()
    self._items.clear()
    self.endResetModel()
```

### Updating Items
```python
def updateItem(self, row: int, item):
    """Update single item data."""
    if not 0 <= row < len(self._items):
        return
    self._items[row] = item
    index = self.index(row, 0)
    self.dataChanged.emit(index, index, [])  # All roles changed

def updateItemField(self, row: int, field: str, value):
    """Update specific field and emit only relevant roles."""
    if not 0 <= row < len(self._items):
        return
    setattr(self._items[row], field, value)
    index = self.index(row, 0)
    # Emit only the changed role
    role = self._field_to_role.get(field, Qt.ItemDataRole.DisplayRole)
    self.dataChanged.emit(index, index, [role])
```

### Moving Items
```python
def moveItem(self, from_row: int, to_row: int) -> bool:
    if from_row == to_row:
        return False
    if not (0 <= from_row < len(self._items) and 0 <= to_row < len(self._items)):
        return False
    
    # Qt's move semantics: destination is BEFORE the target position
    dest_row = to_row if to_row < from_row else to_row + 1
    
    if not self.beginMoveRows(QModelIndex(), from_row, from_row, QModelIndex(), dest_row):
        return False
    
    item = self._items.pop(from_row)
    actual_dest = to_row if to_row < from_row else to_row
    self._items.insert(actual_dest, item)
    self.endMoveRows()
    return True
```

### Batch Operations (Efficient)
```python
def setItems(self, items: list):
    """Replace all items efficiently."""
    self.beginResetModel()
    self._items = list(items)  # Copy to avoid external mutation
    self.endResetModel()

def batchUpdate(self, updates: dict[int, object]):
    """Update multiple items, emit single dataChanged."""
    if not updates:
        return
    
    for row, item in updates.items():
        if 0 <= row < len(self._items):
            self._items[row] = item
    
    rows = list(updates.keys())
    top_left = self.index(min(rows), 0)
    bottom_right = self.index(max(rows), self.columnCount() - 1)
    self.dataChanged.emit(top_left, bottom_right, [])
```

---

## Proxy Models

### Sort/Filter Proxy
```python
from PySide6.QtCore import QSortFilterProxyModel

class FilteredModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._filter_text = ""
        self._filter_role = Qt.ItemDataRole.DisplayRole
    
    def setFilterText(self, text: str):
        self._filter_text = text.lower()
        self.invalidateFilter()
    
    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        if not self._filter_text:
            return True
        
        index = self.sourceModel().index(source_row, 0, source_parent)
        data = self.sourceModel().data(index, self._filter_role)
        return self._filter_text in str(data).lower()
    
    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        """Custom sort logic."""
        left_data = self.sourceModel().data(left, self.sortRole())
        right_data = self.sourceModel().data(right, self.sortRole())
        
        # Handle None values
        if left_data is None:
            return True
        if right_data is None:
            return False
        
        # Type-aware comparison
        if isinstance(left_data, (int, float)) and isinstance(right_data, (int, float)):
            return left_data < right_data
        return str(left_data).lower() < str(right_data).lower()

# Usage
proxy = FilteredModel()
proxy.setSourceModel(my_model)
proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
proxy.setSortRole(Roles.TimestampRole)
view.setModel(proxy)
```

### Multi-Column Filter
```python
class MultiColumnFilterModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._column_filters = {}  # column -> filter text
    
    def setColumnFilter(self, column: int, text: str):
        if text:
            self._column_filters[column] = text.lower()
        else:
            self._column_filters.pop(column, None)
        self.invalidateFilter()
    
    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:
        model = self.sourceModel()
        for col, filter_text in self._column_filters.items():
            index = model.index(row, col, parent)
            data = str(model.data(index, Qt.ItemDataRole.DisplayRole)).lower()
            if filter_text not in data:
                return False
        return True
```

---

## Delegates

### Custom Item Delegate
```python
from PySide6.QtWidgets import QStyledItemDelegate, QWidget, QStyleOptionViewItem
from PySide6.QtCore import QModelIndex

class ProgressDelegate(QStyledItemDelegate):
    """Render progress bars in table cells."""
    
    def paint(self, painter, option: QStyleOptionViewItem, index: QModelIndex):
        progress = index.data(Qt.ItemDataRole.DisplayRole)
        if not isinstance(progress, (int, float)):
            super().paint(painter, option, index)
            return
        
        # Draw background
        painter.save()
        painter.fillRect(option.rect, option.palette.base())
        
        # Draw progress bar
        progress_rect = option.rect.adjusted(2, 2, -2, -2)
        progress_rect.setWidth(int(progress_rect.width() * progress / 100))
        painter.fillRect(progress_rect, Qt.GlobalColor.green)
        
        # Draw text
        painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, f"{progress}%")
        painter.restore()


class ComboBoxDelegate(QStyledItemDelegate):
    """Editable combo box in cells."""
    
    def __init__(self, items: list[str], parent=None):
        super().__init__(parent)
        self._items = items
    
    def createEditor(self, parent: QWidget, option, index: QModelIndex) -> QWidget:
        from PySide6.QtWidgets import QComboBox
        editor = QComboBox(parent)
        editor.addItems(self._items)
        return editor
    
    def setEditorData(self, editor, index: QModelIndex):
        value = index.data(Qt.ItemDataRole.EditRole)
        idx = editor.findText(value)
        if idx >= 0:
            editor.setCurrentIndex(idx)
    
    def setModelData(self, editor, model, index: QModelIndex):
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)
```

---

## Common Mistakes

### ❌ Missing begin/end Brackets
```python
# WRONG: View won't update correctly
def addItem(self, item):
    self._items.append(item)  # No notification!

# CORRECT: Always bracket modifications
def addItem(self, item):
    self.beginInsertRows(QModelIndex(), len(self._items), len(self._items))
    self._items.append(item)
    self.endInsertRows()
```

### ❌ Invalid Index Access
```python
# WRONG: Crashes on invalid index
def data(self, index, role):
    return self._items[index.row()].name

# CORRECT: Validate index first
def data(self, index, role):
    if not index.isValid():
        return None
    if not 0 <= index.row() < len(self._items):
        return None
    if role != Qt.ItemDataRole.DisplayRole:
        return None
    return self._items[index.row()].name
```

### ❌ Returning Mutable References
```python
# WRONG: External code can mutate internal data
def data(self, index, role):
    if role == Roles.DataRole:
        return self._items[index.row()]  # Direct reference!

# CORRECT: Return copy or immutable
def data(self, index, role):
    if role == Roles.DataRole:
        return copy.copy(self._items[index.row()])
```

### ❌ Model Reset for Small Changes
```python
# WRONG: Reset destroys selection, scroll position
def updateItem(self, row, item):
    self.beginResetModel()
    self._items[row] = item
    self.endResetModel()

# CORRECT: Use dataChanged for updates
def updateItem(self, row, item):
    self._items[row] = item
    idx = self.index(row, 0)
    self.dataChanged.emit(idx, idx, [])
```

### ❌ Wrong rowCount for Trees
```python
# WRONG: Returns total items for tree model
def rowCount(self, parent):
    return len(self._all_items)  # Wrong for trees!

# CORRECT: Return children of parent
def rowCount(self, parent):
    if parent.column() > 0:
        return 0
    parent_item = parent.internalPointer() if parent.isValid() else self._root
    return parent_item.childCount()
```
