import re
from datetime import timedelta


def normalize(input: str):
    return input.strip().lower()


def parse_interval(input: str):
    match = re.match(r"^(\d+)([hms])$", normalize(input))

    if not match:
        raise ValueError(f"Неверный формат времени: {input}")

    value = int(match.group(1))
    unit = match.group(2)

    match unit:
        case "h":
            return timedelta(hours=value)
        case "m":
            return timedelta(minutes=value)
        case "s":
            return timedelta(seconds=value)
        case _:
            raise ValueError(f"Неизвестная единица измерения: {unit}")
