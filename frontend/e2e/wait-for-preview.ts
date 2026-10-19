// The e2e container can start before `vite preview` listens: wait for it (and for the app
// behind its proxy) instead of failing the first test.
export default async function waitForPreview(): Promise<void> {
  const base = process.env.E2E_BASE_URL ?? 'http://web-preview:4173';
  const deadline = Date.now() + 60_000;
  let last = '';
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${base}/internal/v1/session`);
      if (response.status === 401) return;
      last = `HTTP ${response.status}`;
    } catch (error) {
      last = String(error);
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error(`the preview server at ${base} did not answer within 60 s (${last})`);
}
