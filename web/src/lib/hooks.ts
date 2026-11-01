import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";

export interface Resource<T> {
  data: T | undefined;
  error: ApiError | undefined;
  loading: boolean;
  reload: () => void;
}

/** Fetch a resource; optionally poll every `interval` ms while `poll` returns true. */
export function useApi<T = any>(
  path: string | null,
  { interval, poll }: { interval?: number; poll?: (data: T | undefined) => boolean } = {},
): Resource<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<ApiError>();
  const [loading, setLoading] = useState(Boolean(path));
  const [tick, setTick] = useState(0);
  const pollRef = useRef(poll);
  useEffect(() => {
    pollRef.current = poll;
  }, [poll]);

  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (!path) return;
    let cancelled = false;
    setLoading(true);
    api<T>(path)
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setError(undefined);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof ApiError ? err : new ApiError(0, "network_error", String(err)));
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [path, tick]);

  useEffect(() => {
    if (!interval || !path) return;
    const timer = window.setInterval(() => {
      if (!pollRef.current || pollRef.current(data)) setTick((t) => t + 1);
    }, interval);
    return () => window.clearInterval(timer);
  }, [interval, path, data]);

  return { data, error, loading, reload };
}

/** Run an async action and expose its pending/error state. */
export function useAction<A extends unknown[], R>(fn: (...args: A) => Promise<R>) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError>();
  const run = useCallback(
    async (...args: A): Promise<R | undefined> => {
      setPending(true);
      setError(undefined);
      try {
        return await fn(...args);
      } catch (err) {
        setError(err instanceof ApiError ? err : new ApiError(0, "error", String(err)));
        return undefined;
      } finally {
        setPending(false);
      }
    },
    [fn],
  );
  return { run, pending, error, clearError: () => setError(undefined) };
}
