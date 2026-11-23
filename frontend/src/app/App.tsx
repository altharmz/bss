import { WriteLockProvider } from '../api/WriteLock';
import { UiKitScreen } from '../screens/UiKit';
import { getJson } from '../api/client';
import { useResource } from '../api/useResource';
import { BillingRunsScreen } from '../screens/BillingRuns';
import { OrdersScreen } from '../screens/Orders';
import { ProductsScreen } from '../screens/Products';
import { PrepaidCommandsScreen } from '../screens/PrepaidCommands';
import { CatalogScreen } from '../screens/Catalog';
import { PaymentsScreen } from '../screens/Payments';
import { BalancesScreen } from '../screens/Balances';
import { BillsScreen } from '../screens/GlobalBills';
import { PartiesScreen } from '../screens/Parties';
import { BillingSettingsScreen } from '../screens/BillingSettings';
import { FinancialAccountsScreen } from '../screens/FinancialAccounts';
import { NotFoundScreen } from '../screens/placeholders';
import { SubscriberCardScreen } from '../screens/SubscriberCard';
import { SubscribersScreen } from '../screens/Subscribers';
import { Signups } from '../screens/CareOperator';
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
import { LoggingSettingsScreen } from '../screens/LoggingSettings';
import { UsersScreen } from '../screens/Users';
import { IntegrationsScreen } from '../screens/Integrations';
import { NumbersScreen } from '../screens/Numbers';
import { SimsScreen } from '../screens/Sims';
import { PortRequestsScreen } from '../screens/PortRequests';
import { ReportsScreen } from '../screens/Reports';
import { LifecycleSettingsScreen } from '../screens/LifecycleSettings';
import { WholesaleInvoiceScreen, WholesaleScreen } from '../screens/Wholesale';

const CONTEXT = '/internal/v1/context';

function Screen() {
  const { route } = useRouter();
  switch (route.name) {
    case 'uiKit':
      return <UiKitScreen />;
    case 'numbers':
      return <NumbersScreen />;
    case 'sims':
      return <SimsScreen />;
    case 'ports':
      return <PortRequestsScreen />;
    case 'users':
      return <UsersScreen />;
    case 'integrations':
      return <IntegrationsScreen />;
    case 'signIn':
    case 'password':
      return null;
    case 'subscribers':
      return <SubscribersScreen />;
    case 'careSignups':
      return <Signups />;
    case 'subscriber':
      return <SubscriberCardScreen key={route.accountId} accountId={route.accountId} />;
    case 'catalog':
      return <CatalogScreen />;
    case 'orders':
      return <OrdersScreen />;
    case 'products':
      return <ProductsScreen />;
    case 'billingRuns':
      return <BillingRunsScreen />;
    case 'prepaidCommands':
      return <PrepaidCommandsScreen />;
    case 'financialAccounts':
      return <FinancialAccountsScreen />;
    case 'parties':
      return <PartiesScreen />;
    case 'billingSettings':
      return <BillingSettingsScreen />;
    case 'logging':
      return <LoggingSettingsScreen />;
    case 'payments':
      return <PaymentsScreen />;
    case 'bills':
      return <BillsScreen />;
    case 'wholesale':
      return route.invoiceId === undefined ? (
        <WholesaleScreen />
      ) : (
        <WholesaleInvoiceScreen key={route.invoiceId} id={route.invoiceId} />
      );
    case 'balances':
      return <BalancesScreen />;
    case 'reports':
      return <ReportsScreen />;
    case 'prepaidLifecycle':
      return <LifecycleSettingsScreen />;
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
          <WriteLockProvider>
            <ToastProvider>
              <SessionGate>
                <Root />
              </SessionGate>
            </ToastProvider>
          </WriteLockProvider>
        </NavVersionProvider>
      </CrumbsProvider>
    </RouterProvider>
  );
}
