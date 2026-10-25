/**
 * Граница ошибок импорта ленивого чанка (TASKS.md, п.7 «Code-splitting»).
 *
 * После релиза хэши файлов меняются: вкладка, открытая до релиза, загрузит
 * уже несуществующий чанк раздела. lazyRoute() сначала сама один раз
 * перезагружает документ, а сюда попадаем, только если вторая попытка тоже
 * провалилась (или ошибка не про чанк). Без границы получился бы белый экран:
 * React не умеет «дорисовать» страницу, у которой не приехал модуль.
 *
 * Шапка здесь нужна, чтобы с битого раздела можно было уйти на другой, —
 * AppShell лежит в индексном чанке и доступен всегда.
 */
import { Component, type ReactNode } from "react";
import { AppShell } from "./AppShell";
import { clearReloadFlag } from "../lazyRoute";

type Props = { children: ReactNode; /** wide-раскладка шапки, как у страницы */ wide?: boolean; /** смена раздела сбрасывает ошибку прошлой вкладки */ resetKey?: string };
type State = { error: Error | null };

export class ChunkErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidUpdate(prev: Props) {
    // ошибка относится к прошлому разделу — уходим в чистое состояние
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null });
  }

  render() {
    const err = this.state.error;
    if (!err) return this.props.children;
    return (
      <AppShell wide={this.props.wide}>
        <div className="panel p-5 max-w-[680px]" role="alert">
          <h2 className="panel-title mb-2">Раздел не загрузился</h2>
          <p className="text-sm leading-relaxed text-ink-300">
            Кабинет, скорее всего, обновился, пока эта страница была открыта: файл раздела лежит под
            другим именем. Перезагрузите страницу — вход, сохранённая раскладка и фильтры не потеряются.
          </p>
          <div className="mt-4">
            <button
              className="btn-primary text-xs"
              onClick={() => {
                clearReloadFlag();
                window.location.reload();
              }}
            >
              Перезагрузить страницу
            </button>
          </div>
          {/* техническую строку не прячем: по ней админ отличит промах чанка от ошибки в коде */}
          <p className="num mt-3 text-[11px] break-words text-ink-400">{err.message}</p>
        </div>
      </AppShell>
    );
  }
}
