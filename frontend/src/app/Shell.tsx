import { Fragment, useState, type ReactNode } from 'react';
import { loadRuns } from '../api/bss';
import { countFailedCommands } from '../api/commands';
import { useResource } from '../api/useResource';
import { businessDate, utcLabel } from '../format/dates';
import { useCrumbs, useNavVersion, type SystemContext } from './context';
import { visibleNav } from './nav';
import { Link, useRouter } from './router';
import './shell.css';
import { Avatar, Badge, Icon, Segmented, Tag } from '../ui/kit';
import { readTheme, saveTheme, type Theme } from './theme';
import { SignOut, useSession } from './session';

// The frame around every screen: sidebar (brand, navigation, operator) and top bar
// (breadcrumbs, business date, time zone). The sections are data in nav.ts (#414). Billing
// runs shows a red "failed" badge when the latest run failed, Prepaid commands when a command
// failed (no item counts, #51).

/** Whether the latest run failed: one run, newest first. Reloaded when the Billing runs
 * screen sees its latest run change (useRefreshNav). */
function useRunsFailed(): boolean {
  const version = useNavVersion();
  const runs = useResource(`nav:runs:${version}`, (signal) => loadRuns(signal, 1));
  return runs.status === 'ok' && runs.data.items[0]?.status === 'failed';
}

/** Whether any prepaid command failed technically. The Prepaid commands screen asks for a
 * reload (useRefreshNav) after a retry. */
function useCommandsNav(): boolean {
  const version = useNavVersion();
  const failed = useResource(`nav:commands:${version}`, countFailedCommands);
  return failed.status === 'ok' && failed.data > 0;
}

export function operatorInitials(login: string, displayName?: string): string {
  const words = displayName?.trim().split(/\s+/);
  return (
    words && words.length >= 2 ? `${words[0]?.[0] ?? ''}${words[1]?.[0] ?? ''}` : login.slice(0, 2)
  ).toUpperCase();
}

function driftLabel(context: SystemContext): string | null {
  const wallDate = businessDate(context.now, context.timeZone);
  const days = Math.round((Date.parse(context.businessDate) - Date.parse(wallDate)) / 86_400_000);
  if (days === 0) return null;
  const count = Math.abs(days);
  return `${count} ${count === 1 ? 'day' : 'days'} ${days < 0 ? 'behind' : 'ahead of'} the wall clock`;
}

export function Sidebar({ context }: { context: SystemContext }) {
  const session = useSession();
  const { route } = useRouter();
  const runsFailed = useRunsFailed();
  const commandsFailed = useCommandsNav();
  const failed = { runs: runsFailed, commands: commandsFailed };
  const sections = visibleNav(session?.role === 'admin');
  return (
    <aside className="bss-sidebar">
      <div className="bss-brand">
        <span className="name">BSS Admin</span>
        <Tag>{context.environment.toUpperCase()}</Tag>
      </div>
      <nav className="bss-nav" aria-label="Main">
        {sections.map((section) => (
          <Fragment key={section.key}>
            <div className="section" data-ui={`ui.shell.section.${section.key}`}>
              {section.label}
            </div>
            {section.items.map((item) => {
              const active = item.routes.includes(route.name);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  title={item.tmf}
                  className={active ? 'is-active' : undefined}
                  aria-current={active ? 'page' : undefined}
                >
                  {item.label}
                  {item.badge && failed[item.badge] && <Badge kind="failed">failed</Badge>}
                </Link>
              );
            })}
          </Fragment>
        ))}
      </nav>
      <div className="bss-operator">
        <details data-ui="ui.session.menu">
          <summary data-ui="ui.shell.operator">
            <Avatar
              name={session?.username ?? ''}
              initials={operatorInitials(session?.username ?? '')}
            />
            <span className="identity">
              <span className="who">{session?.username}</span>
              <span className="role">{session?.role}</span>
            </span>
          </summary>
          <nav aria-label="User menu">
            <ThemeChoice />
            <Link href="/change-password">Change password</Link>
            <SignOut />
          </nav>
        </details>
      </div>
    </aside>
  );
}

const THEMES: { key: Theme; label: string }[] = [
  { key: 'system', label: 'System' },
  { key: 'light', label: 'Light' },
  { key: 'dark', label: 'Dark' },
];

export function ThemeChoice() {
  const [theme, setTheme] = useState<Theme>(readTheme);
  return (
    <div className="theme-choice" data-ui="ui.shell.theme">
      <Segmented
        label="Theme"
        options={THEMES}
        value={theme}
        onChange={(next) => {
          saveTheme(next);
          setTheme(next);
        }}
      />
    </div>
  );
}

export function TopBar({ context }: { context: SystemContext | null }) {
  const crumbs = useCrumbs();
  const drift = context ? driftLabel(context) : null;
  return (
    <header className="bss-topbar">
      <nav className="bss-crumbs" aria-label="Breadcrumbs">
        {crumbs.map((crumb, i) => (
          <Fragment key={`${i}-${crumb.label}`}>
            {i > 0 && <Icon name="chevron-right" size={14} className="sep" />}
            {crumb.href !== undefined && i < crumbs.length - 1 ? (
              <Link href={crumb.href}>{crumb.label}</Link>
            ) : (
              <span className="current" aria-current="page">
                {crumb.label}
              </span>
            )}
          </Fragment>
        ))}
      </nav>
      {context !== null && (
        <div
          className={`bss-business-date${drift ? ' is-warning' : ''}`}
          data-ui="ui.shell.businessDate"
        >
          <span className="label">Business date</span>
          <span className="value">
            {context.businessDate}
            <span className="zone">{utcLabel(context.utcOffset)}</span>
          </span>
          {drift && <span className="drift">{drift}</span>}
        </div>
      )}
    </header>
  );
}

export function Shell({
  context,
  children,
}: {
  context: SystemContext | null;
  children: ReactNode;
}) {
  return (
    <div className="bss-root" data-ui="ui.shell">
      {context !== null ? <Sidebar context={context} /> : <aside className="bss-sidebar" />}
      <div className="bss-main">
        <TopBar context={context} />
        <main className="bss-content">{children}</main>
      </div>
    </div>
  );
}
