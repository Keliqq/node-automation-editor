from nodes.actions import ClickNode, KeyPressNode, MoveMouseNode, TypeTextNode
from nodes.debug import PrintValueNode
from nodes.events import StartNode
from nodes.flow import IfElseNode, WaitNode, WhileLoopNode
from nodes.ocr import ReadTextFromScreenNode
from nodes.vision import FindImageOnScreenNode, PixelColorMatchNode

ALL_NODES = [
    StartNode,
    IfElseNode,
    WhileLoopNode,
    WaitNode,
    FindImageOnScreenNode,
    PixelColorMatchNode,
    ReadTextFromScreenNode,
    ClickNode,
    MoveMouseNode,
    KeyPressNode,
    TypeTextNode,
    PrintValueNode,
]
