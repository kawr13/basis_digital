/**
 * Ленивая загрузка страниц (TASKS.md, п.7 «Code-splitting»).
 *
 * Каждая страница — отдельный чанк: экран входа больше не тянет «Статистику»
 * (1357 строк), админку, постановку судов и отчёты. Разметку маршрутов и
 * проверку прав это не трогает: guards живут в App.tsx (индексный чанк) и
 * отрабатывают до того, как запросится чанк страницы.
 *
 * Промах чанка. После релиза хэши в именах файлов меняются, и страница,
 * открытая до релиза, тянет уже несуществующий файл — import() падает. Такую
 * ошибку ловим здесь и ровно ОДИН раз перезагружаем документ: браузер получит
 * свежий index.html с новыми хэшами. Флаг лежит в sessionStorage, поэтому
 * «вечный» цикл перезагрузок невозможен, а если не помогло — ошибку поднимаем
 * выше, и её честно покажет ChunkErrorBoundary.
 */
import { lazy, type ComponentType, type LazyExoticComponent } from "react";

const RELOAD_FLAG = "statcab.chunk-reload";

function readFlag(): boolean {
  try {
    return sessionStorage.getItem(RELOAD_FLAG) === "1";
  } catch {
    // приватный режим/запрет хранилища: считаем, что перезагрузки ещё не было
    return false;
  }
}

function writeFlag() {
  try {
    sessionStorage.setItem(RELOAD_FLAG, "1");
  } catch {
    /* без хранилища остаёмся без защиты от цикла — лучше, чем без страниц */
  }
}

/** Снять флаг: кнопка «Перезагрузить страницу» в границе ошибок обязана дать второй шанс. */
export function clearReloadFlag() {
  try {
    sessionStorage.removeItem(RELOAD_FLAG);
  } catch {
    /* игнорируем */
  }
}

/**
 * Ошибка именно динамического импорта (упавший/отсутствующий чанк), а не ошибка
 * внутри страницы. Формулировки различаются по браузерам: Chrome — «Failed to
 * fetch dynamically imported module», Firefox — «error loading dynamically
 * imported module», Safari — «Importing a module script failed».
 */
function isChunkError(e: unknown): boolean {
  const msg = e instanceof Error ? e.message : String(e ?? "");
  return /dynamically imported module|importing a module script failed|error loading dynamically imported module|loading chunk \d+ failed|unable to preload css/i.test(
    msg,
  );
}

/** Тот же React.lazy, но с одноразовой перезагрузкой при промахе чанка. */
export function lazyRoute<T extends ComponentType<any>>(
  factory: () => Promise<{ default: T }>,
): LazyExoticComponent<T> {
  return lazy(async () => {
    try {
      const mod = await factory();
      clearReloadFlag(); // чанк приехал — индекс актуален, флаг больше не нужен
      return mod;
    } catch (e) {
      if (isChunkError(e) && !readFlag()) {
        writeFlag();
        window.location.reload();
        // Документ перезагружается, а промис «вешаем»: иначе на кадр мелькнула
        // бы плашка «раздел не загрузился» там, где сейчас всё откроется.
        return await new Promise<never>(() => undefined);
      }
      throw e;
    }
  });
}
