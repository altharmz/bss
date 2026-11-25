import { useSetCrumbs } from '../app/context';
import { SETTINGS_TABS, hrefOf, useRouter, type SettingsTab } from '../app/router';
import { Segmented } from '../ui/kit';
import { BillingSettingsTab } from './BillingSettings';
import { DunningSettingsTab } from './DunningSettings';
import { LifecycleSettingsTab } from './LifecycleSettings';
import './screens.css';

// Settings (#467, docs/ui.md): System's admin-only page for the billing settings, the prepaid
// lifecycle and the dunning steps, each a tab at its own URL (/settings/<tab>). The tabs are
// the screens that were under Billing and Prepaid, moved as they were.

export function SettingsScreen({ tab }: { tab: SettingsTab }) {
  const { navigate } = useRouter();
  useSetCrumbs(
    [{ label: 'Settings', href: '/settings' }, { label: SETTINGS_TABS[tab].label }],
    `settings:${tab}`,
  );
  const tabs = (
    <div className="bss-toolbar">
      <Segmented
        label="Settings tabs"
        value={tab}
        onChange={(key) => navigate(hrefOf({ name: 'settings', tab: key }))}
        options={(Object.keys(SETTINGS_TABS) as SettingsTab[]).map((key) => ({
          key,
          label: SETTINGS_TABS[key].label,
          ui: `ui.settings.tab.${key}`,
        }))}
      />
    </div>
  );
  if (tab === 'prepaidLifecycle') return <LifecycleSettingsTab tabs={tabs} />;
  if (tab === 'dunning') return <DunningSettingsTab tabs={tabs} />;
  return <BillingSettingsTab tabs={tabs} />;
}
