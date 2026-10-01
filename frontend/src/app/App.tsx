import { useEffect, useState } from 'react';
import type { components } from '../api/generated/internal';

type Context = components['schemas']['Context'];

// Scaffold only (stage 5 checkpoint 3): proves the dev proxy, the generated types and the
// fonts. The real shell - sidebar, top bar, router - is checkpoint 4.
export function App() {
  const [context, setContext] = useState<Context | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch('/internal/v1/context')
      .then(async (response) => {
        if (!response.ok) throw new Error(`GET /internal/v1/context → ${response.status}`);
        setContext((await response.json()) as Context);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  return (
    <main style={{ padding: 'var(--pad-page)' }}>
      <h1 style={{ fontSize: 'var(--fs-18)', fontWeight: 600 }}>BSS Console</h1>
      {error && <p className="neg">{error}</p>}
      {context && (
        <p>
          Business date <span className="num">{context.businessDate}</span> · Time zone{' '}
          <span className="num">UTC{context.utcOffset}</span>
        </p>
      )}
    </main>
  );
}
