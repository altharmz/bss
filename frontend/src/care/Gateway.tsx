import { useEffect, useRef } from 'react';
export function Gateway({ onToken }: { onToken: (token: string) => void }) {
  const frame = useRef<HTMLIFrameElement>(null);
  useEffect(() => {
    function receive(event: MessageEvent) {
      if (
        event.origin === location.origin &&
        event.source === frame.current?.contentWindow &&
        event.data?.type === 'stub-card-token' &&
        typeof event.data.token === 'string'
      )
        onToken(event.data.token);
    }
    window.addEventListener('message', receive);
    return () => window.removeEventListener('message', receive);
  }, [onToken]);
  return <iframe title="Test payment gateway" ref={frame} src="/care/stub-gateway.html" />;
}
