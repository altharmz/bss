import { getJson } from '../api/client';
import { useResource } from '../api/useResource';
import { CatalogScreen, NotFoundScreen } from '../screens/placeholders';
import { SubscriberCardScreen } from '../screens/SubscriberCard';
import { SubscribersScreen } from '../screens/Subscribers';
import { ErrorBlock } from '../ui/states';
import { Panel, ToastProvider } from '../ui/kit';
import '../ui/ui.css';
import { CrumbsProvider, SystemContextProvider, type SystemContext } from './context';
import { RouterProvider, useRouter } from './router';
import { Shell } from './Shell';

const CONTEXT = '/internal/v1/context';

function Screen() {
  const { route } = useRouter();
  switch (route.name) {
    case 'subscribers':
      return <SubscribersScreen />;
    case 'subscriber':
      return <SubscriberCardScreen accountId={route.accountId} />;
    case 'catalog':
      return <CatalogScreen />;
    case 'notFound':
      return <NotFoundScreen path={route.path} />;
  }
}

/** The business date and time zone come first: no screen can show a date without them. */
function Root() {
  const context = useResource(CONTEXT, (signal) => getJson<SystemContext>(CONTEXT, signal));
  return (
    <Shell context={context.status === 'ok' ? context.data : null}>
      {context.status === 'error' && (
        <Panel>
          <ErrorBlock
            title="Failed to load the business date"
            error={context.error}
            onRetry={context.retry}
          />
        </Panel>
      )}
      {context.status === 'ok' && (
        <SystemContextProvider value={context.data}>
          <Screen />
        </SystemContextProvider>
      )}
    </Shell>
  );
}

export function App() {
  return (
    <RouterProvider>
      <CrumbsProvider>
        <ToastProvider>
          <Root />
        </ToastProvider>
      </CrumbsProvider>
    </RouterProvider>
  );
}
