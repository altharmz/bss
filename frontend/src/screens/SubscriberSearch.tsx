import { useState } from 'react';
import { searchSubscribers } from '../api/bss';
import { useResource } from '../api/useResource';
import { accountLabel } from '../format/ids';
import { Combobox } from '../ui/combobox';
import { useSettled } from '../ui/paging';

// The subscriber search of the cross-subscriber Orders and Products screens (#455): the
// Subscribers list's own search (GET /internal/v1/subscriber?q=), as a combobox. Choosing a
// row hands its account to the screen, which shows it as the removable account chip.

export function SubscriberSearch({
  ui,
  onChoose,
}: {
  ui: string;
  onChoose: (account: { id: string; name: string }) => void;
}) {
  const [typed, setTyped] = useState('');
  const q = useSettled(typed).trim();
  const found = useResource(`subscriber-search:${q}`, (signal) =>
    q === ''
      ? Promise.resolve([])
      : searchSubscribers({ q, state: null, mode: null }, 0, signal).then((page) => page.items),
  );
  const rows = found.status === 'ok' ? found.data : [];
  return (
    <Combobox
      ui={ui}
      ariaLabel="Subscriber"
      placeholder="Subscriber: name, BA id or number"
      value=""
      options={rows.map((row) => ({
        value: row.id,
        label: `${row.customerName} · ${row.name} · ${accountLabel(row.id)}`,
      }))}
      onQueryChange={setTyped}
      // the server matched the text (a number or an id need not be in the label)
      match={() => true}
      onChange={(id) => {
        const row = rows.find((r) => r.id === id);
        if (row) onChoose({ id: row.id, name: row.name });
      }}
    />
  );
}
