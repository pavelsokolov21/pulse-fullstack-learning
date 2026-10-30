class MonitorNotFoundError(Exception):
    """Монитора с таким id нет в хранилище."""

    def __init__(self, monitor_id: int) -> None:
        super().__init__(f"Монитор {monitor_id} не найден")
        self.monitor_id = monitor_id


class DuplicateMonitorError(Exception):
    """Монитор с таким URL уже есть."""

    def __init__(self, url: str, existing_id: int) -> None:
        super().__init__(f"Монитор с URL {url} уже существует (id {existing_id})")
        self.url = url
        self.existing_id = existing_id
