class MonitorNotFoundError(Exception):
    """Монитора с таким id нет в хранилище."""

    def __init__(self, monitor_id: int) -> None:
        super().__init__(f"Монитор {monitor_id} не найден")
        self.monitor_id = monitor_id
