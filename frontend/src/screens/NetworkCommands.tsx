import { useState } from 'react';
import {
  loadNetworkCommands,
  NETWORK_KIND_LABEL,
  retryNetworkCommand,
  type NetworkCommand,
  type NetworkOwner,
} from '../api/network';
import { explain } from '../api/client';
import { useResource } from '../api/useResource';
import { useSystemContext } from '../app/context';
import { businessDate, clockTime } from '../format/dates';
import { Button, KeyValue, StatusPill } from '../ui/kit';
import { Note } from '../ui/Note';

// The network step of an order or product (docs/decisions/network.md): every command the
// billing worker sends to the network for an order item, its state, attempts and last
// error. The one write is Retry of an `exhausted` command; a refusal is final, the fix is a
// new order. Shown only when there are commands (other offerings send nothing).

function when(instant: string | null, timeZone: string): string {
  return instant === null
    ? '—'
    : `${businessDate(instant, timeZone)} ${clockTime(instant, timeZone)}`;
}

export function NetworkCommands({ owner }: { owner: NetworkOwner }) {
  const key = 'orderId' in owner ? `order:${owner.orderId}` : `product:${owner.productId}`;
  const list = useResource(`network-commands:${key}`, (signal) =>
    loadNetworkCommands(owner, signal),
  );
  if (list.status === 'loading') return null;
  if (list.status === 'error')
    return (
      <Note tone="negative" data-ui="ui.network.commands">
        Failed to load the network commands ({list.error.message}){' '}
        <Button small onClick={list.retry}>
          Retry
        </Button>
      </Note>
    );
  if (list.data.length === 0) return null;
  return (
    <section className="bss-dialog-section" data-ui="ui.network.commands">
      <h3>Network</h3>
      {list.data.map((command) => (
        <NetworkCommandRow key={command.id} command={command} onChanged={list.retry} />
      ))}
    </section>
  );
}

function NetworkCommandRow({
  command,
  onChanged,
}: {
  command: NetworkCommand;
  onChanged: () => void;
}) {
  const context = useSystemContext();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function retry() {
    setBusy(true);
    setProblem(null);
    try {
      await retryNetworkCommand(command.id);
      onChanged();
    } catch (error) {
      setProblem(explain(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="bss-form" data-ui="ui.network.command">
      <KeyValue
        items={[
          ['Command', NETWORK_KIND_LABEL[command.kind]],
          ['State', <StatusPill key="s" kind="network" status={command.state} />],
          ['Due', command.dueDate],
          ['Attempts', String(command.attempts)],
          ['Last attempt', when(command.attemptedAt, context.timeZone)],
          ...(command.state === 'pending' && command.nextAttemptAt !== null
            ? [['Next attempt', when(command.nextAttemptAt, context.timeZone)] as [string, string]]
            : []),
          ...(command.state === 'confirmed'
            ? [['Confirmed', when(command.confirmedAt, context.timeZone)] as [string, string]]
            : []),
          ...(command.lastError !== null
            ? [['Last error', command.lastError] as [string, string]]
            : []),
          ...(command.refusalCode !== null
            ? [['Refusal code', command.refusalCode] as [string, string]]
            : []),
        ]}
      />
      {command.state === 'refused' && (
        <Note tone="warning" data-ui="ui.network.refused">
          The network refused this command. A refusal is final: place a new order.
        </Note>
      )}
      {problem !== null && <Note tone="negative">{problem}</Note>}
      {command.state === 'exhausted' && (
        <div>
          <Button
            variant="primary"
            data-ui="ui.network.retry"
            disabled={busy}
            onClick={() => void retry()}
          >
            Retry
          </Button>
        </div>
      )}
    </div>
  );
}
