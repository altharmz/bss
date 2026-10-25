import { getJson } from '../api/client';
import { useResource } from '../api/useResource';
import { BillingRunsScreen } from '../screens/BillingRuns';
import { OrdersScreen } from '../screens/Orders';
import { PrepaidCommandsScreen } from '../screens/PrepaidCommands';
import { CatalogScreen } from '../screens/Catalog';
import { PartiesScreen } from '../screens/Parties';
import { FinancialAccountsScreen } from '../screens/FinancialAccounts';
import { NotFoundScreen } from '../screens/placeholders';
import { SubscriberCardScreen } from '../screens/SubscriberCard';
import { SubscribersScreen } from '../screens/Subscribers';
import { ErrorBlock } from '../ui/states';
import { Panel, ToastProvider } from '../ui/kit';
import '../ui/ui.css';
import {
  CrumbsProvider,
  NavVersionProvider,
  SystemContextProvider,
  type SystemContext,
} from './context';
import { RouterProvider, useRouter } from './router';
import { Shell } from './Shell';
import { SessionGate } from './session';
import { UsersScreen } from '../screens/Users';
import { NumbersScreen } from '../screens/Numbers';

const CONTEXT = '/internal/v1/context';

function Screen() {
  const { route } = useRouter();
  switch (route.name) {
    case 'numbers':
      return <NumbersScreen />;
    case 'users':
      return <UsersScreen />;
    case 'signIn':
    case 'password':
      return null;
    case 'subscribers':
      return <SubscribersScreen />;
    case 'subscriber':
      return <SubscriberCardScreen key={route.accountId} accountId={route.accountId} />;
    case 'catalog':
      return <CatalogScreen />;
    case 'orders':
      return <OrdersScreen />;
    case 'billingRuns':
      return <BillingRunsScreen />;
    case 'prepaidCommands':
      return <PrepaidCommandsScreen />;
    case 'financialAccounts':
      return <FinancialAccountsScreen />;
    case 'parties':
      return <PartiesScreen />;
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
        <NavVersionProvider>
          <ToastProvider>
            <SessionGate>
              <Root />
            </SessionGate>
          </ToastProvider>
        </NavVersionProvider>
      </CrumbsProvider>
    </RouterProvider>
  );
}
