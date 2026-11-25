import { useEffect, useState } from 'react';
import { loadLogging, saveLogging, type LogChange, type LogSettings } from '../api/logging';
import { usePolled } from '../api/usePolled';
import { useSetCrumbs, useSystemContext } from '../app/context';
import { businessDate, clockTime } from '../format/dates';
import { Badge, Button, KeyValue, PageHeader, Panel, SelectField, useToast } from '../ui/kit';
import { Note } from '../ui/Note';
import { ErrorBlock, SkeletonRows } from '../ui/states';
import { Footer, useWrite } from './FinancialForms';

const REFRESH_MS = 30_000;
const DEBUG_MINUTES = [15, 30, 60] as const;
const BASE_LEVELS = ['INFO', 'WARNING'] as const;

/** Whole minutes left, rounded up, or null when debug is off or over. */
export function minutesLeft(until: string | null, nowMs: number): number | null {
  if (until === null) return null;
  const ms = Date.parse(until) - nowMs;
  return ms > 0 ? Math.ceil(ms / 60_000) : null;
}

/** The current time, ticking every `everyMs`: the countdown is display only. */
function useNow(everyMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(id);
  }, [everyMs]);
  return now;
}

export function LoggingSettingsScreen() {
  useSetCrumbs([{ label: 'Logging' }], 'logging');
  // A user never gets here (App.tsx: System is admin-only, #467); the API refuses them too.
  const logging = usePolled('logging', loadLogging, () => true, REFRESH_MS);
  return (
    <div className="bss-screen" data-ui="ui.logging.settings">
      <PageHeader title="Logging" />
      <Panel>
        <h2>Log level</h2>
        <Note tone="info">
          The app, the worker and the MCP connector pick a change up within 15 seconds. Debug always
          ends by itself.
        </Note>
        {logging.status === 'loading' && (
          <SkeletonRows
            columns={[
              { key: 'setting', label: 'Setting', width: '1fr' },
              { key: 'value', label: 'Value', width: '1fr' },
            ]}
          />
        )}
        {logging.status === 'error' && (
          <ErrorBlock
            title="Failed to load the log level"
            error={logging.error}
            onRetry={logging.retry}
          />
        )}
        {logging.status === 'ok' && (
          <LoggingState settings={logging.data} refresh={logging.refresh} />
        )}
      </Panel>
    </div>
  );
}

function LoggingState({ settings, refresh }: { settings: LogSettings; refresh: () => void }) {
  const { timeZone } = useSystemContext();
  const toast = useToast();
  const now = useNow(5_000);
  const left = minutesLeft(settings.debugUntil, now);
  const [base, setBase] = useState<string>(settings.baseLevel ?? '');
  const write = useWrite((message) => {
    toast(message);
    refresh();
  });
  const send = (body: LogChange, message: string) =>
    void write.run(async () => {
      await saveLogging(body);
      return message;
    });
  const baseChanged = base !== (settings.baseLevel ?? '');
  // the countdown may pass the end before the next read: show what applies then
  const effective =
    left === null && settings.effectiveLevel === 'DEBUG'
      ? (settings.baseLevel ?? settings.envLevel)
      : settings.effectiveLevel;
  const baseLabel = settings.baseLevel ?? `Default (${settings.envLevel})`;
  const debugText =
    left === null || settings.debugUntil === null
      ? 'off'
      : `Debug until ${clockTime(settings.debugUntil, timeZone)}, ${left} min left`;
  return (
    <>
      <KeyValue
        items={[
          [
            'Effective level',
            <Badge key="e" kind={effective === 'DEBUG' ? 'info' : 'count'}>
              {effective}
            </Badge>,
          ],
          ['Base level', baseLabel],
          ['Debug', debugText],
          [
            'Last change',
            settings.updatedAt === null
              ? 'never'
              : `${settings.updatedBy ?? 'unknown'}, ${businessDate(settings.updatedAt, timeZone)} ${clockTime(settings.updatedAt, timeZone)}`,
          ],
        ]}
      />
      <fieldset className="bss-form bss-write-fields" disabled={write.busy || write.unsure}>
        <label>
          Base level
          <SelectField
            aria-label="Base level"
            value={base}
            onChange={(e) => setBase(e.target.value)}
          >
            <option value="">Default ({settings.envLevel})</option>
            {BASE_LEVELS.map((level) => (
              <option key={level} value={level}>
                {level}
              </option>
            ))}
          </SelectField>
        </label>
        <Button
          variant="primary"
          busyLabel="Saving…"
          disabled={!baseChanged || write.busy}
          onClick={() =>
            send(
              { baseLevel: base === '' ? null : (base as 'INFO' | 'WARNING') },
              'Base log level saved',
            )
          }
        >
          Save
        </Button>
      </fieldset>
      <Footer
        write={write}
        onRefresh={() => {
          refresh();
          toast('Log level refreshed');
        }}
        left={
          left !== null && (
            <Button onClick={() => send({ debugMinutes: 0 }, 'Debug stopped')}>Stop debug</Button>
          )
        }
      >
        {DEBUG_MINUTES.map((n) => (
          <Button key={n} onClick={() => send({ debugMinutes: n }, `Debug on for ${n} min`)}>
            {`Debug ${n} min`}
          </Button>
        ))}
      </Footer>
    </>
  );
}
