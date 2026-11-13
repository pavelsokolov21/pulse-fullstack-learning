import { describe, expect, it } from "vitest";

import { createSseParser, type SseMessage } from "@/lib/sse";

function parse(...chunks: string[]): SseMessage[] {
  const messages: SseMessage[] = [];
  const feed = createSseParser((m) => messages.push(m));
  chunks.forEach(feed);
  return messages;
}

describe("createSseParser", () => {
  it("parses event, data and id", () => {
    expect(parse('event: task\nid: 5-0\ndata: {"a":1}\n\n')).toEqual([
      { event: "task", data: '{"a":1}', id: "5-0" },
    ]);
  });

  it("names a nameless event `message`", () => {
    expect(parse("data: hi\n\n")[0].event).toBe("message");
  });

  it("joins several data lines with a newline", () => {
    expect(parse("data: a\ndata: b\ndata: c\n\n")[0].data).toBe("a\nb\nc");
  });

  it("strips exactly one space after the colon", () => {
    expect(parse("data:  two\n\n")[0].data).toBe(" two");
    expect(parse("data:none\n\n")[0].data).toBe("none");
  });

  it("ignores comments such as keep-alive pings", () => {
    expect(parse(": ping\n\n: stream open\n\n")).toEqual([]);
  });

  it("does not deliver a message without data", () => {
    expect(parse("event: task\nid: 1-0\n\n")).toEqual([]);
  });

  it("delivers several messages from one chunk, in order", () => {
    const messages = parse("data: 1\n\ndata: 2\n\ndata: 3\n\n");

    expect(messages.map((m) => m.data)).toEqual(["1", "2", "3"]);
  });

  it("waits for the blank line before delivering", () => {
    const messages: SseMessage[] = [];
    const feed = createSseParser((m) => messages.push(m));

    feed("data: partial\n");
    expect(messages).toEqual([]);
    feed("\n");
    expect(messages).toHaveLength(1);
  });

  it("survives chunks split anywhere, even inside a line", () => {
    const text = 'event: task\nid: 7-1\ndata: {"x":"привет"}\n\n';
    for (let cut = 1; cut < text.length; cut++) {
      expect(parse(text.slice(0, cut), text.slice(cut))).toEqual([
        { event: "task", data: '{"x":"привет"}', id: "7-1" },
      ]);
    }
  });

  it("survives one-character chunks", () => {
    const text = "event: task\ndata: ok\n\n";

    expect(parse(...text.split(""))).toHaveLength(1);
  });

  it.each([
    ["\r\n", "CRLF"],
    ["\r", "CR"],
    ["\n", "LF"],
  ])("accepts %j line endings (%s)", (eol) => {
    const text = `event: task${eol}data: ok${eol}${eol}`;

    // Следом приходит комментарий: одинокий «\r» в конце потока может быть началом
    // «\r\n», поэтому парсер ждёт следующий символ.
    expect(parse(text, `: ping${eol}`)).toEqual([
      { event: "task", data: "ok", id: null },
    ]);
  });

  it("does not split CRLF when the chunk ends between CR and LF", () => {
    expect(parse("data: ok\r", "\n\r", "\n")).toEqual([
      { event: "message", data: "ok", id: null },
    ]);
  });

  it("does not read a CRLF cut in the middle as an extra blank line", () => {
    // Если принять «\r» за конец строки, следующий «\n» стал бы пустой строкой и
    // оборвал бы сообщение на «a».
    const messages = parse("data: a\r", "\ndata: b\r\n\r\n");

    expect(messages.map((m) => m.data)).toEqual(["a\nb"]);
  });

  it("keeps the last id for the following messages", () => {
    const messages = parse(
      "id: 1-0\ndata: a\n\ndata: b\n\nid: 2-0\ndata: c\n\n",
    );

    expect(messages.map((m) => m.id)).toEqual(["1-0", "1-0", "2-0"]);
  });

  it("resets the event name between messages", () => {
    const messages = parse("event: task\ndata: a\n\ndata: b\n\n");

    expect(messages.map((m) => m.event)).toEqual(["task", "message"]);
  });

  it("ignores an id with a NUL character", () => {
    expect(parse("id: a\0b\ndata: x\n\n")[0].id).toBeNull();
  });

  it("ignores unknown fields and retry", () => {
    expect(parse("retry: 3000\nfoo: bar\ndata: x\n\n")).toEqual([
      { event: "message", data: "x", id: null },
    ]);
  });

  it("treats a line without a colon as a field with an empty value", () => {
    expect(parse("data\ndata\n\n")[0].data).toBe("\n");
  });
});
