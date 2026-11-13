import { createSseParser } from "@/lib/sse";
import type { TaskEntry, TaskEvent } from "@/lib/tasks";

export type StreamStatus =
  | "connecting" // первое подключение
  | "open" // поток открыт, события идут
  | "reconnecting" // связь оборвалась, ждём и пробуем снова
  | "forbidden" // роль не позволяет читать журнал
  | "signed-out"; // сессия кончилась

export type TaskStreamOptions = {
  url: string;
  getToken: () => string | null;
  /** Обновляет сессию; `null`, если не вышло. */
  refresh: () => Promise<string | null>;
  onEntry: (entry: TaskEntry) => void;
  onStatus: (status: StreamStatus) => void;
  /** Для тестов. */
  fetchImpl?: typeof fetch;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
  random?: () => number;
};

const CLEAN_END_DELAY_MS = 500;
const MAX_DELAY_MS = 15_000;
const MAX_CONSECUTIVE_401 = 2;

/**
 * Пауза перед переподключением после `failures` неудач подряд: 1, 2, 4, 8, 15 с.
 * Разброс ±25%, чтобы вкладки, потерявшие сервер одновременно, не вернулись разом.
 */
export function reconnectDelay(
  failures: number,
  random: () => number = Math.random,
): number {
  const base = Math.min(MAX_DELAY_MS, 1000 * 2 ** Math.max(0, failures - 1));
  return Math.round(base * (0.75 + random() * 0.5));
}

function defaultSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

function parseEvent(data: string): TaskEvent | null {
  try {
    const value: unknown = JSON.parse(data);
    if (
      typeof value === "object" &&
      value !== null &&
      "check_id" in value &&
      typeof value.check_id === "number" &&
      "state" in value &&
      typeof value.state === "string" &&
      "at" in value &&
      typeof value.at === "string"
    ) {
      return value as TaskEvent;
    }
  } catch {
    // Битое сообщение пропускаем: один плохой кадр не должен рвать поток.
  }
  return null;
}

/**
 * Подключается к потоку событий задач и держит подключение.
 *
 * Что делает сверх голого `fetch`:
 * - токен кладёт в заголовок `Authorization` (`EventSource` так не умеет);
 * - после обрыва продолжает с `Last-Event-ID`: сервер отдаёт только пропущенное;
 * - при обрыве ждёт с нарастающей паузой, при 401 обновляет токен;
 * - при 403 останавливается: повтор не поможет.
 *
 * Сервер сам закрывает поток раз в несколько минут: это не ошибка, клиент
 * переподключается почти сразу.
 *
 * Возвращает функцию остановки.
 */
export function connectTaskStream(options: TaskStreamOptions): () => void {
  const {
    url,
    getToken,
    refresh,
    onEntry,
    onStatus,
    fetchImpl = (input, init) => fetch(input, init),
    sleep = defaultSleep,
    random = Math.random,
  } = options;
  const controller = new AbortController();
  const { signal } = controller;
  let lastId: string | null = null;

  async function readStream(body: ReadableStream<Uint8Array>) {
    const decoder = new TextDecoder();
    const feed = createSseParser((message) => {
      if (message.event !== "task" || message.id === null) return;
      const event = parseEvent(message.data);
      if (event === null) return;
      lastId = message.id;
      onEntry({ id: message.id, event });
    });
    const reader = body.getReader();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      feed(decoder.decode(value, { stream: true }));
    }
  }

  async function run() {
    let failures = 0;
    let unauthorized = 0;
    onStatus("connecting");

    while (!signal.aborted) {
      try {
        const headers: Record<string, string> = { Accept: "text/event-stream" };
        const token = getToken();
        if (token) headers.Authorization = `Bearer ${token}`;
        if (lastId !== null) headers["Last-Event-ID"] = lastId;

        const response = await fetchImpl(url, {
          headers,
          signal,
          cache: "no-store",
        });

        if (response.status === 401) {
          unauthorized += 1;
          const fresh =
            unauthorized <= MAX_CONSECUTIVE_401 ? await refresh() : null;
          if (fresh !== null) continue; // токен новый: повторяем сразу
          if (getToken() === null) {
            onStatus("signed-out"); // refresh отказал: сессии больше нет
            return;
          }
          throw new Error("unauthorized"); // сбой сети при refresh: подождём
        }
        if (response.status === 403) {
          onStatus("forbidden");
          return;
        }
        const type = response.headers.get("content-type") ?? "";
        if (
          !response.ok ||
          !response.body ||
          !type.includes("text/event-stream")
        ) {
          throw new Error(`unexpected response ${response.status}`);
        }

        unauthorized = 0;
        failures = 0;
        onStatus("open");
        await readStream(response.body);
        // Сервер закрыл поток по сроку: не ошибка, подключаемся снова.
        if (signal.aborted) return;
        onStatus("reconnecting");
        await sleep(CLEAN_END_DELAY_MS, signal);
      } catch {
        if (signal.aborted) return;
        failures += 1;
        onStatus("reconnecting");
        await sleep(reconnectDelay(failures, random), signal);
      }
    }
  }

  void run();
  return () => controller.abort();
}
