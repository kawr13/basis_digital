/**
 * Состояние блока «настройки времени выполнения» для вкладки 1С и вкладки
 * Настроек: оба редактора читают и пишут один и тот же конверт, поэтому
 * значения на двух вкладках не могут разойтись.
 */
import { useCallback, useEffect, useState } from "react";
import { api, type SchedulerReport, type SettingsEnvelope, type SettingsPatch } from "../../api";
import { errText } from "./ui";

export type AdminSettings = {
  env: SettingsEnvelope | null;
  loading: boolean;
  error: string;
  /** ответ планировщика после последнего сохранения — «следующая загрузка каждые N мин» */
  scheduler: SchedulerReport | null;
  reload: () => Promise<void>;
  save: (patch: SettingsPatch) => Promise<SettingsEnvelope | null>;
  reset: (keys: string[]) => Promise<SettingsEnvelope | null>;
};

export function useAdminSettings(): AdminSettings {
  const [env, setEnv] = useState<SettingsEnvelope | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [scheduler, setScheduler] = useState<SchedulerReport | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setEnv(await api.adminSettings());
      setError("");
    } catch (e) {
      setError(errText(e, "Не удалось загрузить настройки"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const save = useCallback(async (patch: SettingsPatch) => {
    try {
      const next = await api.adminSaveSettings(patch);
      setEnv(next);
      setScheduler(next.scheduler ?? null);
      setError("");
      return next;
    } catch (e) {
      setError(errText(e, "Настройки не сохранены"));
      return null;
    }
  }, []);

  const reset = useCallback(async (keys: string[]) => {
    try {
      const next = await api.adminResetSettings(keys);
      setEnv(next);
      setScheduler(next.scheduler ?? null);
      setError("");
      return next;
    } catch (e) {
      setError(errText(e, "Сброс не выполнен"));
      return null;
    }
  }, []);

  return { env, loading, error, scheduler, reload, save, reset };
}

/** Человекочитаемый ответ планировщика после сохранения настроек. */
export function schedulerNote(rep: SchedulerReport | null): string {
  if (!rep) return "";
  if (!rep.scheduler) return "Планировщик в этом процессе недоступен — загрузка только по кнопке «Синхронизировать сейчас».";
  if (!rep.enabled) return "Синхронизация по расписанию выключена — задача снята, витрина обновляется только вручную.";
  return `Синхронизация по расписанию: следующая загрузка каждые ${rep.interval_minutes} мин (задача: ${rep.job ?? "нет"}).`;
}
