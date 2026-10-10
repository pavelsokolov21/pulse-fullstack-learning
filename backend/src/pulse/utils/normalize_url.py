import re
from urllib.parse import urlsplit, urlunsplit

from pulse.decorators.validation import validate_trimmed_not_empty

# Схема по RFC 3986. Искать «://» нужно только в начале строки: внутри query
# или fragment (`?to=http://x.com`) это обычные данные, а не схема.
_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")

_DEFAULT_PORTS = {"http": 80, "https": 443}


@validate_trimmed_not_empty(argument_name="url")
def normalize_url(url: str) -> str:
    """Приводит URL монитора к каноничному виду.

    - добавляет `https://`, если схемы нет;
    - схему и хост приводит к нижнему регистру, путь и query не трогает;
    - отбрасывает fragment, порт по умолчанию и одиночный `/` в пути;
    - отвергает чужие схемы, логин и пароль в URL, пустой хост, неверный порт.
    """
    candidate = url.strip()

    # urlsplit молча удаляет \t, \n и \r, поэтому проверяем их заранее.
    if any(ch.isspace() for ch in candidate):
        raise ValueError("Невалидный формат URL: есть пробельные символы")

    if not _SCHEME_RE.match(candidate):
        candidate = f"https://{candidate}"

    try:
        parts = urlsplit(candidate)
        port = parts.port
    except ValueError as e:
        raise ValueError(f"Неверный URL или порт: {url}") from e

    scheme = parts.scheme  # urlsplit уже привёл схему к нижнему регистру

    if scheme not in _DEFAULT_PORTS:
        raise ValueError(f"Неверный протокол: {scheme}")

    host = parts.hostname  # в нижнем регистре, IPv6 без скобок

    if not host:
        raise ValueError("Пустой хост")

    if parts.username is not None or parts.password is not None:
        raise ValueError("Логин и пароль в URL не поддерживаются")

    if ":" in host:
        host = f"[{host}]"

    netloc = host if port in (None, _DEFAULT_PORTS[scheme]) else f"{host}:{port}"
    path = "" if parts.path == "/" else parts.path

    return urlunsplit((scheme, netloc, path, parts.query, ""))
