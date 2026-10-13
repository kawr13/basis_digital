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

/** Разрешения пользователя на «Постановку судов». */
export type VesselRights = { view: boolean; edit: boolean; delete: boolean };

const RIGHT_LEVEL: Record<string, number> = { none: 0, view: 1, edit: 2, manage: 3 };

type UserWithRights = {
  role: string;
  vessel_right?: string | null;
  vessel_rights?: VesselRights | null;
};

/**
 * Та же логика, что в `app/services/vessels.py::rights_for`: личная графа
 * пользователя (`users.vessel_right`) перекрывает ролевые настройки.
 *
 * Сначала берём `vessel_rights` — их посчитал бэкенд и прислал со входа и `/me`:
 * так вкладка «Постановка» и кнопка «Новая постановка» не мигают, пока не ответит
 * `/api/settings/public`, и не остаются, если тот запрос недоступен.
 */
export function resolveVesselRights(
  user: UserWithRights | null,
  settings: PublicSettings | null,
): VesselRights {
  if (!user) return { view: false, edit: false, delete: false };
  if (user.vessel_rights) return user.vessel_rights;
  const personal = user.vessel_right;
  if (personal) {
    const level = RIGHT_LEVEL[personal] ?? 0;
    return { view: level >= 1, edit: level >= 2, delete: level >= 3 };
  }
  return {
    view: roleAllowed(settings, "vessels.view_roles", user.role),
    edit: roleAllowed(settings, "vessels.edit_roles", user.role),
    delete: roleAllowed(settings, "vessels.delete_roles", user.role),
  };
}

export const VESSEL_RIGHT_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "по роли (как в настройках)" },
  { value: "none", label: "нет доступа" },
  { value: "view", label: "только смотреть" },
  { value: "edit", label: "ставить и править" },
  { value: "manage", label: "полный доступ, с удалением" },
];

export function vesselRightLabel(right?: string | null): string {
  return VESSEL_RIGHT_OPTIONS.find((o) => o.value === (right ?? ""))?.label ?? "по роли";
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
