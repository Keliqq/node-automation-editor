#!/usr/bin/env python3
"""
PySide6 Code Reviewer - Automated Anti-Pattern Detection

Usage:
    python review.py <file_or_directory> [--verbose] [--json]

Detects common anti-patterns in PySide6/Qt code.
"""

import ast
import re
import sys
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class Issue:
    code: str
    severity: str  # CRITICAL, HIGH, MEDIUM, LOW
    message: str
    file: str
    line: int
    snippet: str = ""
    fix_hint: str = ""


@dataclass
class ReviewResult:
    file: str
    issues: list[Issue] = field(default_factory=list)
    
    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "CRITICAL")
    
    @property
    def high_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "HIGH")


class PySide6Reviewer(ast.NodeVisitor):
    """AST-based code reviewer for PySide6 patterns."""
    
    def __init__(self, filename: str, source: str):
        self.filename = filename
        self.source = source
        self.lines = source.splitlines()
        self.issues: list[Issue] = []
        self.in_qthread_run = False
        self.in_paint_event = False
        self.current_class = None
    
    def add_issue(self, code: str, severity: str, message: str, 
                  node: ast.AST, fix_hint: str = ""):
        snippet = self.lines[node.lineno - 1] if node.lineno <= len(self.lines) else ""
        self.issues.append(Issue(
            code=code,
            severity=severity,
            message=message,
            file=self.filename,
            line=node.lineno,
            snippet=snippet.strip(),
            fix_hint=fix_hint
        ))
    
    def visit_ClassDef(self, node: ast.ClassDef):
        self.current_class = node.name
        
        # Check for QThread subclass
        bases = [self._get_name(b) for b in node.bases]
        is_qthread = any(b in ('QThread', 'QtCore.QThread') for b in bases)
        
        self.generic_visit(node)
        self.current_class = None
    
    def visit_FunctionDef(self, node: ast.FunctionDef):
        # Track if we're in QThread.run()
        if node.name == 'run' and self.current_class:
            self.in_qthread_run = True
        
        # Track if we're in paintEvent
        if node.name == 'paintEvent':
            self.in_paint_event = True
        
        # Check for missing @Slot decorator on likely slot methods
        if node.name.startswith('on_') or node.name.startswith('handle_'):
            has_slot = any(
                self._get_name(d) in ('Slot', 'QtCore.Slot')
                for d in node.decorator_list
            )
            if not has_slot:
                self.add_issue(
                    "AP-010", "LOW",
                    f"Method '{node.name}' looks like a slot but missing @Slot decorator",
                    node,
                    "Add @Slot() decorator for type safety and optimization"
                )
        
        self.generic_visit(node)
        self.in_qthread_run = False
        self.in_paint_event = False
    
    def visit_Call(self, node: ast.Call):
        func_name = self._get_name(node.func)
        
        # Check for GUI operations in QThread.run()
        if self.in_qthread_run:
            gui_methods = {
                'setText', 'setValue', 'setItem', 'addItem', 'clear',
                'show', 'hide', 'setVisible', 'setEnabled', 'setStyleSheet',
                'setPixmap', 'setIcon', 'update', 'repaint'
            }
            if func_name in gui_methods or (
                isinstance(node.func, ast.Attribute) and 
                node.func.attr in gui_methods
            ):
                self.add_issue(
                    "AP-001", "CRITICAL",
                    f"Potential GUI operation '{func_name}' in worker thread",
                    node,
                    "Use signals to communicate with main thread"
                )
        
        # Check for blocking calls
        blocking_calls = {'sleep', 'time.sleep', 'requests.get', 'requests.post'}
        if func_name in blocking_calls:
            if not self.in_qthread_run:  # Only flag in main thread context
                self.add_issue(
                    "AP-003", "CRITICAL",
                    f"Blocking call '{func_name}' may freeze UI",
                    node,
                    "Move to worker thread or use async"
                )
        
        # Check for QPixmap in potential thread context
        if func_name in ('QPixmap', 'QtGui.QPixmap') and self.in_qthread_run:
            self.add_issue(
                "AP-002", "CRITICAL",
                "QPixmap is not thread-safe",
                node,
                "Use QImage instead, convert to QPixmap in main thread"
            )
        
        # Check for object creation in paintEvent
        if self.in_paint_event:
            paint_allocations = {'QFont', 'QPen', 'QBrush', 'QColor', 'QPainterPath'}
            if func_name in paint_allocations:
                self.add_issue(
                    "AP-017", "MEDIUM",
                    f"Creating {func_name} in paintEvent causes GC pressure",
                    node,
                    "Pre-create in __init__ and reuse"
                )
        
        # Check for repaint() instead of update()
        if isinstance(node.func, ast.Attribute):
            if node.func.attr == 'repaint':
                self.add_issue(
                    "AP-018", "MEDIUM",
                    "repaint() bypasses Qt's paint optimization",
                    node,
                    "Use update() for queued, coalesced repaints"
                )
        
        # Check for deprecated exec_()
        if isinstance(node.func, ast.Attribute):
            if node.func.attr == 'exec_':
                self.add_issue(
                    "AP-022", "LOW",
                    "exec_() is deprecated in Qt 6",
                    node,
                    "Use exec() instead"
                )
        
        # Check for old-style signal connection
        if func_name == 'connect' and len(node.args) >= 2:
            for arg in node.args:
                if isinstance(arg, ast.Call):
                    if self._get_name(arg.func) == 'SIGNAL':
                        self.add_issue(
                            "AP-009", "MEDIUM",
                            "Old-style SIGNAL() syntax is deprecated",
                            node,
                            "Use: object.signal.connect(slot)"
                        )
        
        self.generic_visit(node)
    
    def visit_Attribute(self, node: ast.Attribute):
        # Check for old enum syntax
        old_enums = {
            ('Qt', 'AlignCenter'): 'Qt.AlignmentFlag.AlignCenter',
            ('Qt', 'AlignLeft'): 'Qt.AlignmentFlag.AlignLeft',
            ('Qt', 'AlignRight'): 'Qt.AlignmentFlag.AlignRight',
            ('Qt', 'Horizontal'): 'Qt.Orientation.Horizontal',
            ('Qt', 'Vertical'): 'Qt.Orientation.Vertical',
            ('Qt', 'LeftButton'): 'Qt.MouseButton.LeftButton',
            ('Qt', 'RightButton'): 'Qt.MouseButton.RightButton',
            ('Qt', 'black'): 'Qt.GlobalColor.black',
            ('Qt', 'white'): 'Qt.GlobalColor.white',
            ('Qt', 'red'): 'Qt.GlobalColor.red',
        }
        
        if isinstance(node.value, ast.Name):
            key = (node.value.id, node.attr)
            if key in old_enums:
                self.add_issue(
                    "AP-021", "LOW",
                    f"Old enum syntax: {key[0]}.{key[1]}",
                    node,
                    f"Use: {old_enums[key]}"
                )
        
        self.generic_visit(node)
    
    def visit_Delete(self, node: ast.Delete):
        # Check for direct deletion of potential QObjects
        for target in node.targets:
            if isinstance(target, ast.Attribute):
                attr_name = target.attr
                if any(s in attr_name.lower() for s in ['widget', 'worker', 'thread', 'timer']):
                    self.add_issue(
                        "AP-006", "HIGH",
                        f"Direct deletion of '{attr_name}' may crash with pending signals",
                        node,
                        "Use deleteLater() for QObjects"
                    )
        self.generic_visit(node)
    
    def _get_name(self, node) -> str:
        """Extract name from AST node."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return f"{self._get_name(node.value)}.{node.attr}"
        elif isinstance(node, ast.Call):
            return self._get_name(node.func)
        return ""


class RegexReviewer:
    """Regex-based pattern detection for issues AST can't catch."""
    
    def __init__(self, filename: str, source: str):
        self.filename = filename
        self.source = source
        self.lines = source.splitlines()
        self.issues: list[Issue] = []
    
    def review(self) -> list[Issue]:
        self._check_signal_naming()
        self._check_model_patterns()
        self._check_missing_parents()
        return self.issues
    
    def _check_signal_naming(self):
        # Check for camelCase signals (should be snake_case in Python)
        pattern = re.compile(r'^\s*([a-z]+[A-Z][a-zA-Z]*)\s*=\s*Signal\(')
        for i, line in enumerate(self.lines, 1):
            match = pattern.search(line)
            if match:
                name = match.group(1)
                # Allow camelCase for QML notify signals (typically end with Changed)
                if not name.endswith('Changed'):
                    self.issues.append(Issue(
                        code="AP-024",
                        severity="LOW",
                        message=f"Signal '{name}' uses camelCase, prefer snake_case",
                        file=self.filename,
                        line=i,
                        snippet=line.strip(),
                        fix_hint=f"Rename to {self._to_snake_case(name)}"
                    ))
    
    def _check_model_patterns(self):
        # Check for model modifications without begin/end
        append_pattern = re.compile(r'self\._items\.(append|extend|insert|remove|clear)\(')
        begin_pattern = re.compile(r'self\.begin(Insert|Remove|Reset|Move)')
        
        for i, line in enumerate(self.lines, 1):
            if append_pattern.search(line):
                # Check surrounding lines for begin/end
                context_start = max(0, i - 5)
                context_end = min(len(self.lines), i + 5)
                context = '\n'.join(self.lines[context_start:context_end])
                
                if not begin_pattern.search(context):
                    self.issues.append(Issue(
                        code="AP-013",
                        severity="CRITICAL",
                        message="Model modification without begin/end notification",
                        file=self.filename,
                        line=i,
                        snippet=line.strip(),
                        fix_hint="Wrap with beginInsertRows/endInsertRows etc."
                    ))
    
    def _check_missing_parents(self):
        # Check for widget creation without parent
        widget_pattern = re.compile(
            r'(QLabel|QPushButton|QLineEdit|QWidget|QFrame)\s*\(\s*["\'][^"\']*["\']\s*\)'
        )
        for i, line in enumerate(self.lines, 1):
            if widget_pattern.search(line):
                # Check if parent or layout assignment nearby
                context_end = min(len(self.lines), i + 3)
                context = '\n'.join(self.lines[i-1:context_end])
                
                if 'self' not in line and 'addWidget' not in context and 'layout' not in context.lower():
                    self.issues.append(Issue(
                        code="AP-005",
                        severity="HIGH",
                        message="Widget created without parent",
                        file=self.filename,
                        line=i,
                        snippet=line.strip(),
                        fix_hint="Add parent parameter or add to layout"
                    ))
    
    @staticmethod
    def _to_snake_case(name: str) -> str:
        return re.sub(r'([A-Z])', r'_\1', name).lower().lstrip('_')


def review_file(filepath: Path) -> ReviewResult:
    """Review a single Python file."""
    result = ReviewResult(file=str(filepath))
    
    try:
        source = filepath.read_text(encoding='utf-8')
    except Exception as e:
        result.issues.append(Issue(
            code="ERR",
            severity="HIGH",
            message=f"Could not read file: {e}",
            file=str(filepath),
            line=0
        ))
        return result
    
    # AST-based review
    try:
        tree = ast.parse(source)
        ast_reviewer = PySide6Reviewer(str(filepath), source)
        ast_reviewer.visit(tree)
        result.issues.extend(ast_reviewer.issues)
    except SyntaxError as e:
        result.issues.append(Issue(
            code="ERR",
            severity="HIGH",
            message=f"Syntax error: {e}",
            file=str(filepath),
            line=e.lineno or 0
        ))
    
    # Regex-based review
    regex_reviewer = RegexReviewer(str(filepath), source)
    result.issues.extend(regex_reviewer.review())
    
    # Sort by severity then line number
    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    result.issues.sort(key=lambda i: (severity_order.get(i.severity, 4), i.line))
    
    return result


def review_directory(dirpath: Path) -> Iterator[ReviewResult]:
    """Review all Python files in directory."""
    for pyfile in dirpath.rglob("*.py"):
        # Skip common non-source directories
        if any(part.startswith('.') or part in ('venv', 'env', '__pycache__', 'node_modules') 
               for part in pyfile.parts):
            continue
        yield review_file(pyfile)


def format_issue(issue: Issue, verbose: bool = False) -> str:
    """Format issue for terminal output."""
    severity_colors = {
        "CRITICAL": "\033[91m",  # Red
        "HIGH": "\033[93m",      # Yellow
        "MEDIUM": "\033[94m",    # Blue
        "LOW": "\033[90m",       # Gray
    }
    reset = "\033[0m"
    color = severity_colors.get(issue.severity, "")
    
    output = f"{color}[{issue.code}] {issue.severity}{reset}: {issue.message}\n"
    output += f"  {issue.file}:{issue.line}\n"
    
    if verbose:
        if issue.snippet:
            output += f"  > {issue.snippet}\n"
        if issue.fix_hint:
            output += f"  Fix: {issue.fix_hint}\n"
    
    return output


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="PySide6 Code Reviewer")
    parser.add_argument("path", help="File or directory to review")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show snippets and fix hints")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()
    
    path = Path(args.path)
    
    if not path.exists():
        print(f"Error: {path} does not exist", file=sys.stderr)
        sys.exit(1)
    
    results: list[ReviewResult] = []
    
    if path.is_file():
        results.append(review_file(path))
    else:
        results.extend(review_directory(path))
    
    # Output
    if args.json:
        output = {
            "files_reviewed": len(results),
            "total_issues": sum(len(r.issues) for r in results),
            "critical": sum(r.critical_count for r in results),
            "high": sum(r.high_count for r in results),
            "results": [
                {
                    "file": r.file,
                    "issues": [
                        {
                            "code": i.code,
                            "severity": i.severity,
                            "message": i.message,
                            "line": i.line,
                            "snippet": i.snippet,
                            "fix_hint": i.fix_hint
                        }
                        for i in r.issues
                    ]
                }
                for r in results if r.issues
            ]
        }
        print(json.dumps(output, indent=2))
    else:
        total_issues = 0
        critical_count = 0
        high_count = 0
        
        for result in results:
            if result.issues:
                print(f"\n{'='*60}")
                print(f"File: {result.file}")
                print(f"{'='*60}")
                for issue in result.issues:
                    print(format_issue(issue, args.verbose))
                total_issues += len(result.issues)
                critical_count += result.critical_count
                high_count += result.high_count
        
        # Summary
        print(f"\n{'='*60}")
        print(f"Summary: {len(results)} files, {total_issues} issues")
        print(f"  Critical: {critical_count}")
        print(f"  High: {high_count}")
        
        if critical_count > 0:
            sys.exit(2)
        elif high_count > 0:
            sys.exit(1)


if __name__ == "__main__":
    main()
