from fastapi.routing import APIRoute


def generate_operation_id(route: APIRoute) -> str:
    """`operationId` в схеме: имя функции-обработчика, например `create_monitor`.

    По умолчанию FastAPI склеивает имя, путь и метод (`create_monitor_monitors_post`),
    и генераторы клиентов делают из этого неудобные имена функций и типов.
    Имена обработчиков уникальны в приложении, это проверяет тест схемы.
    """
    return route.name
