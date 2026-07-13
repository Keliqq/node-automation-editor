from core.exec_node import ExecNode


class StartNode(ExecNode):
    """Точка входа графа. Движок запускается от каждой найденной Start-ноды."""

    __identifier__ = "automation.events"
    NODE_NAME = "Старт"

    def __init__(self):
        super().__init__()
        self.add_exec_output()
        self.set_color(60, 130, 70)

    def execute(self, engine):
        engine.log_message.emit("Запуск графа...")
        return "выход"
