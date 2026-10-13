/**
 * Публичные настройки кабинета (те же, что видит админ на вкладке «Настройки»).
 *
 * Нужны, чтобы фронт и бэкенд считали права одинаково: кнопки «Новая постановка»
 * и вкладка «Постановка» прячутся по тем же значениям `vessels.*_roles`, которые
 * проверяет API. Кэшируем на весь сеанс — настройки меняются редко, а запрос копеечный.
 */
import { useEffect, useState } from "react";
import { api } from "./api";

export type PublicSettings = Record<string, number | string>;

let cache: PublicSettings | null = null;
let inflight: Promise<PublicSettings> | null = null;

export function loadPublicSettings(force = false): Promise<PublicSettings> {
  if (cache && !force) return Promise.resolve(cache);
  if (!inflight) {
    inflight = api
      .publicSettings()
      .then((s) => {
        cache = s;
        inflight = null;
        return s;
      })
      .catch((e) => {
        inflight = null;
        throw e;
      });
  }
  return inflight;
}

export function cachedSettings(): PublicSettings | null {
  return cache;
}

export function usePublicSettings(): PublicSettings | null {
  const [settings, setSettings] = useState<PublicSettings | null>(cache);
  useEffect(() => {
    if (cache) {
      setSettings(cache);
      return;
    }
    let alive = true;
    loadPublicSettings()
      .then((s) => alive && setSettings(s))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);
  return settings;
}

/** Роль в списке из настройки вида «admin,top». Если настройки нет — не запрещаем:
 *  окончательное слово всё равно за бэкендом, а рисовать пустой экран из-за
 *  недоступной настройки неправильно. */
export function roleAllowed(settings: PublicSettings | null, key: string, role: string): boolean {
  const raw = settings?.[key];
  if (raw == null || raw === "") return true;
  return String(raw)
    .split(",")
    .map((t) => t.trim())
    .includes(role);
}

const ROLE_LABEL: Record<string, string> = {
  admin: "администратор",
  top: "топ-менеджмент",
  manager: "менеджер",
};

/** «администратор, топ-менеджмент» — для подсказок в интерфейсе. */
export function roleListLabel(settings: PublicSettings | null, key: string, fallback: string): string {
  const raw = settings?.[key] ?? fallback;
  const names = String(raw)
    .split(",")
    .map((t) => ROLE_LABEL[t.trim()] ?? t.trim())
    .filter(Boolean);
  return names.join(", ") || "никто";
}
