/** Одно сообщение потока Server-Sent Events. */
export type SseMessage = {
  /** Имя события (`event:`); без него по стандарту это `message`. */
  event: string;
  /** Поля `data:` одного сообщения, склеенные через перевод строки. */
  data: string;
  /** Последний полученный `id:` (держится и между сообщениями, как в браузере). */
  id: string | null;
};

/**
 * Разбор потока `text/event-stream` по стандарту WHATWG.
 *
 * Зачем свой, если есть `EventSource`: он не умеет слать заголовок `Authorization`,
 * а access-токен у нас живёт только в памяти (не в cookie). Поэтому поток читаем
 * через `fetch`, а разбираем сами.
 *
 * Возвращает функцию, в которую отдают куски текста как пришли: граница куска может
 * проходить где угодно, даже посреди строки или между `\r` и `\n`.
 */
export function createSseParser(
  onMessage: (message: SseMessage) => void,
): (chunk: string) => void {
  let buffer = "";
  let data: string[] = [];
  let event = "";
  let lastId: string | null = null;

  function processLine(line: string) {
    if (line === "") {
      // Пустая строка завершает сообщение. Без data оно не доставляется.
      if (data.length > 0) {
        onMessage({
          event: event || "message",
          data: data.join("\n"),
          id: lastId,
        });
      }
      data = [];
      event = "";
      return;
    }
    if (line.startsWith(":")) return; // комментарий: `: ping` держит соединение
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);

    if (field === "data") data.push(value);
    else if (field === "event") event = value;
    else if (field === "id" && !value.includes("\0")) lastId = value;
    // `retry` и неизвестные поля игнорируем: паузу переподключения ведём сами.
  }

  return (chunk) => {
    buffer += chunk;
    for (;;) {
      const match = /\r\n|\n|\r/.exec(buffer);
      if (!match) return;
      // «\r» в самом конце куска может оказаться началом «\r\n»: ждём продолжения.
      if (match[0] === "\r" && match.index === buffer.length - 1) return;
      processLine(buffer.slice(0, match.index));
      buffer = buffer.slice(match.index + match[0].length);
    }
  };
}
