import re
from datetime import timedelta

# re.ASCII: без него \d матчит любые Unicode-цифры («٥», «５»), а int() их примет.
_INTERVAL_RE = re.compile(r"(\d+)([hms])", re.ASCII)

_UNITS = {
    "h": timedelta(hours=1),
    "m": timedelta(minutes=1),
    "s": timedelta(seconds=1),
}


def _normalize(raw: str) -> str:
    return raw.strip().lower()


def parse_interval(raw: str) -> timedelta:
    match = _INTERVAL_RE.fullmatch(_normalize(raw))

    if not match:
        raise ValueError(f"Неверный формат времени: {raw}")

    value = int(match.group(1))

    if value == 0:
        raise ValueError(f"Интервал должен быть больше нуля: {raw}")

    try:
        # Единица гарантирована регэкспом, поэтому KeyError здесь невозможен.
        return value * _UNITS[match.group(2)]
    except OverflowError as e:
        # timedelta ограничен ~999999999 днями и бросает OverflowError.
        raise ValueError(f"Слишком большой интервал: {raw}") from e
