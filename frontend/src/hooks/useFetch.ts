import { useEffect, useState } from "react";

export interface FetchState<T> {
  data: T | undefined;
  error: unknown;
  loading: boolean;
}

/**
 * 화면이 열릴 때(또는 deps가 바뀔 때) API를 한 번 부르고 결과를 상태로 들고 있는다.
 * 화면을 떠나거나 deps가 바뀌면 이전 요청은 취소한다(AbortController).
 *
 * 예) const { data, error, loading } = useFetch((s) => getStation(bid, s), [bid]);
 */
export function useFetch<T>(
  fn: (signal: AbortSignal) => Promise<T>,
  deps: unknown[],
): FetchState<T> {
  const [state, setState] = useState<FetchState<T>>({
    data: undefined,
    error: undefined,
    loading: true,
  });

  useEffect(() => {
    const ctrl = new AbortController();
    setState({ data: undefined, error: undefined, loading: true });
    fn(ctrl.signal).then(
      (data) => setState({ data, error: undefined, loading: false }),
      (error) => {
        if (!ctrl.signal.aborted) setState({ data: undefined, error, loading: false });
      },
    );
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return state;
}
