from core.exec_node import ExecNode


class PrintValueNode(ExecNode):
    """Отладочная нода: выводит значение в панель «Лог» и пропускает поток
    дальше без изменений. Подключи к «значение» любой выход (число,
    строку, найдено/не найдено и т.д.), чтобы посмотреть, что реально
    приходит на этот провод во время выполнения. Заодно отдаёт то же
    значение через выходной порт «значение» — можно использовать как tap
    посреди цепочки данных, а не только как тупиковый вывод в лог."""

    __identifier__ = "automation.debug"
    NODE_NAME = "Вывести в консоль"

    def __init__(self):
        super().__init__()
        self.add_exec_input()
        self.add_exec_output()
        self.add_data_input("значение", dtype="str", default="")
        self.add_data_input("метка", dtype="str", default="")
        self.add_data_output("значение")
        self.set_color(120, 120, 60)

    def execute(self, engine):
        value = engine.get_input_value(self, "значение")
        label = self.get_property("метка")
        prefix = f"[{label}] " if label else "[Лог] "
        engine.log_message.emit(f"{prefix}{value!r}")
        self.set_output_value("значение", value)
        return "выход"
