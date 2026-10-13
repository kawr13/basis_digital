/**
 * «Почти реальное время».
 *
 * Полноценного сокета здесь нет (и не предвидится: данные приходят из 1С
 * раз в N минут). Вместо него — дешёвый опрос маленького эндпоинта
 * `/api/meta/freshness` раз в 15 секунд: если метка среза изменилась,
 * страница перезапрашивает тяжёлые метрики. Когда вкладка скрыта — не
 * стучимся вовсе, а при возврате обновляемся сразу.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";

/** Интервал опроса свежести. Держим маленьким: ответ — несколько сотен байт. */
export const FRESHNESS_TICK_MS = 15_000;

export type Live = {
  /** сколько секунд назад фронт в последний раз видел ответ freshness */
  ageSeconds: number | null;
  /** бэкенд считает данные устаревшими (успешного синка не было > 2.5 интервалов) */
  stale: boolean;
  /** когда был последний успешный обмен с 1С (ISO) */
  lastSyncAt: string | null;
  /** метка среза данных (ISO) — по ней понимаем, что появились новые данные */
  dataAsOf: string | null;
  /** интервал синхронизации с 1С, мин */
  intervalMinutes: number | null;
  /** был ли хотя бы один успешный опрос */
  ready: boolean;
};

const EMPTY: Live = {
  ageSeconds: null,
  stale: false,
  lastSyncAt: null,
  dataAsOf: null,
  intervalMinutes: null,
  ready: false,
};

export function useLive(onNewData?: () => void): Live {
  const [live, setLive] = useState<Live>(EMPTY);
  // колбэк храним в ref, чтобы не пересоздавать интервал на каждый рендер
  const cb = useRef(onNewData);
  cb.current = onNewData;
  const seen = useRef<string | null>(null);

  const check = useCallback(async () => {
    try {
      const f = await api.freshness();
      setLive({
        ageSeconds: 0,
        stale: f.stale,
        lastSyncAt: f.last_sync?.finished_at ?? null,
        dataAsOf: f.data_as_of,
        intervalMinutes: f.sync_interval_minutes,
        ready: true,
      });
      // данные поменялись — просим страницу перезапросить метрики
      if (seen.current !== null && f.data_as_of !== seen.current) cb.current?.();
      seen.current = f.data_as_of;
    } catch {
      // 401/500/сеть — молча оставляем прошлое состояние: возраст данных
      // не критичный для работы кабинета, падать из-за него нельзя
    }
  }, []);

  useEffect(() => {
    let timer = 0;
    let ageTimer = 0;

    const tick = () => {
      // скрытая вкладка не должна грузить бэкенд и жечь батарею ноутбука
      if (!document.hidden) void check();
    };

    const onVisible = () => {
      if (document.visibilityState === "visible") void check();
    };

    void check();
    timer = window.setInterval(tick, FRESHNESS_TICK_MS);
    // возраст считаем локально — без лишних запросов
    ageTimer = window.setInterval(() => {
      setLive((prev) => (prev.ready ? { ...prev, ageSeconds: (prev.ageSeconds ?? 0) + 15 } : prev));
    }, FRESHNESS_TICK_MS);
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);

    return () => {
      window.clearInterval(timer);
      window.clearInterval(ageTimer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, [check]);

  return live;
}

/** «только что» / «5 мин назад» — для чипа в шапке. */
export function ageLabel(seconds: number | null): string {
  if (seconds == null) return "";
  if (seconds < 45) return "только что";
  const min = Math.round(seconds / 60);
  if (min < 60) return `${min} мин назад`;
  const hours = Math.round(min / 60);
  return hours < 24 ? `${hours} ч назад` : `${Math.round(hours / 24)} дн назад`;
}
