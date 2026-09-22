import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from './api/client';

export interface AsyncState<T> {
  data: T | undefined;
  error: ApiError | undefined;
  loading: boolean;
  reload: () => void;
}

export function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;
  return new ApiError(0, 'client_error', err instanceof Error ? err.message : 'Unexpected error');
}

/** Runs `fn` on mount and whenever `deps` change. A newer call supersedes an older one. */
export function useAsync<T>(fn: () => Promise<T>, deps: readonly unknown[]): AsyncState<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<ApiError | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    let live = true;
    setLoading(true);
    fnRef.current().then(
      (d) => {
        if (!live) return;
        setData(d);
        setError(undefined);
        setLoading(false);
      },
      (e) => {
        if (!live) return;
        setData(undefined);
        setError(toApiError(e));
        setLoading(false);
      },
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}

export function useTitle(title: string) {
  useEffect(() => {
    document.title = `${title} · Sadhik AI`;
  }, [title]);
}
