from pulse.decorators.validation import validate_trimmed_not_empty


@validate_trimmed_not_empty(argument_name="url")
def normalize_url(url: str):
    normalized_url = url.strip()

    base_url, _, _ = normalized_url.partition("#")
    protocol = ""
    rest = base_url

    if "://" in url:
        protocol_part, _, rest_part = base_url.partition("://")
        protocol_normalized = protocol_part.lower()

        if protocol_normalized not in ("http", "https"):
            raise ValueError(f"Неверный протокол: {protocol_normalized}")

        protocol = protocol_normalized + "://"
        rest = rest_part

        if not rest:
            raise ValueError("Пустое значение после протокола")
    else:
        host, _, port_part = rest.partition(":")
        port, _, _ = port_part.partition("/")

        if port and not port.isdigit():
            raise ValueError("Неверный порт")

        protocol = "https://"

    if " " in rest:
        raise ValueError("Невалидный формат хоста")

    if "/" in rest:
        host, path_and_query = rest.split("/", 1)
        path_and_query = "/" + path_and_query
    else:
        host = rest
        path_and_query = ""

    if not host.strip():
        raise ValueError("Пустой хост")

    return f"{protocol}{host.lower()}{path_and_query}"
