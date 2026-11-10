import {
  buildLanes,
  describeEvent,
  fmtMs,
  percent,
  ticks,
  timelineEnd,
  verdict,
  STRATEGY_INFO,
  type ConcurrencyRun,
} from "@/lib/concurrency";

// Ширина полосы не меньше этой доли шкалы, чтобы короткий коммит был виден.
const MIN_WIDTH_PERCENT = 0.7;

/** Результат одного прогона: итог, две дорожки времени и журнал событий. */
export function RaceResult({ run }: { run: ConcurrencyRun }) {
  const end = timelineEnd(run.events, run.elapsed_ms);
  const lanes = buildLanes(run.events);
  const marks = ticks(end);
  const result = verdict(run);
  const info = STRATEGY_INFO[run.strategy];

  return (
    <section className="race-result" aria-label={info.title}>
      <div className="race-head">
        <h2>{info.title}</h2>
        <span className={`verdict verdict-${result.tone}`} role="status">
          {result.text}
        </span>
      </div>

      <dl className="facts">
        <div>
          <dt>Было</dt>
          <dd>{run.initial}</dd>
        </div>
        <div>
          <dt>Ожидали</dt>
          <dd>{run.expected}</dd>
        </div>
        <div>
          <dt>В базе</dt>
          <dd className={result.tone === "bad" ? "bad" : undefined}>
            {run.final}
          </dd>
        </div>
        <div>
          <dt>Версия строки</dt>
          <dd>{run.final_version}</dd>
        </div>
        <div>
          <dt>Всего</dt>
          <dd>{fmtMs(run.elapsed_ms)}</dd>
        </div>
      </dl>

      <div className="race" aria-hidden="true">
        <div className="race-row">
          <span className="race-label" />
          <div className="race-axis">
            {marks.map((t) => (
              <span key={t} style={{ left: `${percent(t, end)}%` }}>
                {t}
              </span>
            ))}
          </div>
        </div>
        {lanes.map((lane) => {
          const call = run.calls.find((c) => c.call === lane.call);
          return (
            <div className="race-row" key={lane.call}>
              <span className="race-label">
                Запрос {lane.call}
                {call && call.attempts > 1 && (
                  <small> ({call.attempts} поп.)</small>
                )}
                {call?.returned != null && (
                  <small> → вернул {call.returned}</small>
                )}
              </span>
              <div className="race-track">
                {marks.map((t) => (
                  <i
                    className="race-grid"
                    key={t}
                    style={{ left: `${percent(t, end)}%` }}
                  />
                ))}
                {lane.segments.map((s) => {
                  const left = percent(s.fromMs, end);
                  const width = Math.max(
                    percent(s.toMs, end) - left,
                    MIN_WIDTH_PERCENT,
                  );
                  return (
                    <div
                      key={`${s.kind}-${s.attempt}-${s.fromMs}`}
                      className={`seg seg-${s.kind}`}
                      style={{ left: `${left}%`, width: `${width}%` }}
                      title={s.title}
                    >
                      {s.label}
                    </div>
                  );
                })}
                {lane.conflicts.map((c) => (
                  <span
                    key={`conflict-${c.attempt}`}
                    className="mark-conflict"
                    style={{ left: `${percent(c.atMs, end)}%` }}
                    title="Версия строки изменилась: запись отклонена"
                  >
                    ✕
                  </span>
                ))}
              </div>
            </div>
          );
        })}
        <p className="race-unit muted">мс от начала прогона</p>
      </div>

      <details className="race-log">
        <summary>Журнал событий ({run.events.length})</summary>
        <table className="table">
          <thead>
            <tr>
              <th>мс</th>
              <th>Запрос</th>
              <th>Что произошло</th>
            </tr>
          </thead>
          <tbody>
            {run.events.map((e, i) => (
              <tr key={i}>
                <td>{e.at_ms}</td>
                <td>{e.call}</td>
                <td>{describeEvent(e)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </section>
  );
}

export function Legend() {
  return (
    <ul className="legend" aria-label="Обозначения">
      <li>
        <i className="swatch seg-wait" /> ждёт блокировку
      </li>
      <li>
        <i className="swatch seg-hold" /> держит прочитанное до записи
      </li>
      <li>
        <i className="swatch seg-commit" /> запись и коммит
      </li>
      <li>
        <span className="mark-conflict inline">✕</span> запись отклонена
      </li>
    </ul>
  );
}
