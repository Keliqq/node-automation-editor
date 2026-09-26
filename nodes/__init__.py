from core.node_loader import discover_user_nodes
from nodes.actions import ClickNode, KeyPressNode, MoveMouseNode, TypeTextNode
from nodes.debug import PrintValueNode
from nodes.events import CallFunctionNode, FunctionNode, StartNode
from nodes.flow import CompareNode, IfElseNode, WaitNode, WhileLoopNode
from nodes.ocr import ReadTextFromScreenNode
from nodes.variables import GetVariableNode, IncrementVariableNode, SetVariableNode
from nodes.vision import FindImageOnScreenNode, PixelColorMatchNode, WaitForImageNode

BUILTIN_NODES = [
    StartNode,
    FunctionNode,
    CallFunctionNode,
    CompareNode,
    IfElseNode,
    WhileLoopNode,
    WaitNode,
    SetVariableNode,
    IncrementVariableNode,
    GetVariableNode,
    FindImageOnScreenNode,
    WaitForImageNode,
    PixelColorMatchNode,
    ReadTextFromScreenNode,
    ClickNode,
    MoveMouseNode,
    KeyPressNode,
    TypeTextNode,
    PrintValueNode,
]

# ALL_NODES — общий изменяемый список: MainWindow дозаписывает в него новые
# классы через .extend(...) при "Обновить список нод", не пересоздавая
# список, иначе модули, уже сделавшие "from nodes import ALL_NODES",
# продолжили бы видеть старую версию.
ALL_NODES = BUILTIN_NODES + discover_user_nodes()
