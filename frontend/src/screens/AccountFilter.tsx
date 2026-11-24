import { useState } from 'react';
import { Id } from '../ui/Id';
import { IconButton } from '../ui/kit';
import { SubscriberSearch } from './SubscriberSearch';

// The billing-account filter of the cross-account money screens (#466): Bills, Payments,
// Dunning, Balances and Prepaid commands. The account comes from ?account=<id> (the links from
// a billing account's card) or from the search box (the Subscribers list's own search, as on
// Orders and Products); it shows as the removable account chip and is kept in the URL.

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The account a link asks for (?account=<id>), or null. */
export function accountFromUrl(): string | null {
  const value = new URLSearchParams(window.location.search).get('account');
  return value !== null && UUID.test(value) ? value.toLowerCase() : null;
}

export interface AccountFilterState {
  account: string | null;
  /** the name of the account chosen in the search, for the chip before rows load */
  chosenName: string | null;
  choose: (chosen: { id: string; name: string }) => void;
  drop: () => void;
}

/** The screen's account filter, at `path` (the screen's route, rewritten in place).
 * `onChange` runs on every change (a screen that pages by hand resets its offset). */
export function useAccountFilter(path: string, onChange?: () => void): AccountFilterState {
  const [account, setAccount] = useState<string | null>(accountFromUrl);
  const [chosenName, setChosenName] = useState<string | null>(null);
  return {
    account,
    chosenName,
    choose: (chosen) => {
      window.history.replaceState(null, '', `${path}?account=${chosen.id}`);
      setAccount(chosen.id);
      setChosenName(chosen.name);
      onChange?.();
    },
    drop: () => {
      window.history.replaceState(null, '', path);
      setAccount(null);
      setChosenName(null);
      onChange?.();
    },
  };
}

/** The search box and, while filtered, the account chip (`<ui>.accountSearch`,
 * `<ui>.accountChip`). `name` is the account's name from the loaded rows, if any. */
export function AccountFilter({
  ui,
  filter,
  name,
}: {
  ui: string;
  filter: AccountFilterState;
  name: string | null;
}) {
  return (
    <>
      <SubscriberSearch ui={`${ui}.accountSearch`} onChoose={filter.choose} />
      {filter.account !== null && (
        <span className="bss-run-tag" data-ui={`${ui}.accountChip`}>
          Account {name ?? filter.chosenName ?? <Id id={filter.account} prefix="BA-" />}{' '}
          <IconButton icon="x" small label="Remove the account filter" onClick={filter.drop} />
        </span>
      )}
    </>
  );
}
