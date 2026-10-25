import { useRef, useState } from 'react';
import {
  orderProductChange,
  type Account,
  type AccountProduct,
  type ProductChange,
} from '../api/bss';
import { explain } from '../api/client';
import type { PendingOrder } from '../api/commands';
import { useSystemContext } from '../app/context';
import { isIsoDate } from '../format/dates';
import { Button, Modal, TextInput } from '../ui/kit';
import { uncertain } from './submissionKey';

// Product actions of the subscriber card: suspend, resume and end on a date, as TMF622 orders
// (docs/next-slices-design.md slice 4, decisions/frontend.md). The server decides every rule;
// the menu hides an item only when the row's own data makes the refusal certain.

export const CHANGE_UI: Record<ProductChange, string> = {
  suspend: 'ui.subscriber.suspend',
  resume: 'ui.subscriber.resume',
  end: 'ui.subscriber.endOnDate',
};

const TITLE: Record<ProductChange, string> = {
  suspend: 'Suspend',
  resume: 'Resume',
  end: 'End on a date',
};

/** The change items the row offers, from what it already shows. */
export function offeredChanges(product: AccountProduct): ProductChange[] {
  const statusPending = product.pendingChanges.some((c) => c.kind === 'status');
  const items: ProductChange[] = [];
  if (product.status === 'active' && !statusPending) items.push('suspend');
  if (
    product.status === 'suspended' &&
    product.suspensionCause !== 'funding' &&
    product.suspensionCause !== 'account' &&
    !statusPending
  ) {
    items.push('resume');
  }
  if (product.terminationDate === null && product.status !== 'pendingTerminate') items.push('end');
  return items;
}

export function ProductActionsMenu({
  items,
  onPick,
}: {
  items: ProductChange[];
  onPick: (change: ProductChange) => void;
}) {
  const [open, setOpen] = useState(false);
  if (items.length === 0) return null;
  return (
    <span className="bss-menu" style={{ position: 'relative', display: 'inline-block' }}>
      <Button
        small
        aria-haspopup="menu"
        aria-expanded={open}
        data-ui="ui.subscriber.productActions"
        onClick={() => setOpen((v) => !v)}
      >
        Actions ▾
      </Button>
      {open && (
        <div
          role="menu"
          style={{
            position: 'absolute',
            top: '100%',
            left: 0,
            zIndex: 5,
            display: 'flex',
            flexDirection: 'column',
            gap: 4,
            padding: 6,
            background: 'var(--surface, #fff)',
            border: '1px solid var(--border, #ccc)',
            borderRadius: 6,
            whiteSpace: 'nowrap',
          }}
        >
          {items.map((item) => (
            <Button
              key={item}
              small
              role="menuitem"
              data-ui={CHANGE_UI[item]}
              onClick={() => {
                setOpen(false);
                onPick(item);
              }}
            >
              {TITLE[item]}…
            </Button>
          ))}
        </div>
      )}
    </span>
  );
}

export function ProductChangeDialog({
  change,
  account,
  product,
  onClose,
  onDone,
  onRefresh,
}: {
  change: ProductChange;
  account: Account;
  product: AccountProduct;
  onClose: () => void;
  onDone: (message: string, accepted?: PendingOrder) => void;
  onRefresh: () => void;
}) {
  const context = useSystemContext();
  const today = context.businessDate;
  const prepaid = account.ratingType === 'prepaid';
  // prepaid status changes take effect today until dated ones exist (slice 8)
  const fixed = prepaid && change !== 'end';
  const [day, setDay] = useState(today);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [unsure, setUnsure] = useState(false);
  const saving = useRef(false);
  const name = product.productOffering.name;
  const valid = isIsoDate(day);
  const expired = prepaid && product.paidThrough != null && product.paidThrough < today;

  const explanation =
    change === 'end'
      ? prepaid
        ? `Unused paid days from ${day} are credited to the balance.`
        : `The product is billed up to the day before ${day}.`
      : prepaid
        ? change === 'resume' && expired
          ? 'The paid interval has ended: resuming starts a new interval paid from the balance.'
          : 'Prepaid status changes take effect today.'
        : 'Billing does not change while a product is suspended: it is charged by its dates.';

  async function submit() {
    if (!valid || saving.current) return;
    saving.current = true;
    setBusy(true);
    setProblem(null);
    setUnsure(false);
    try {
      const result = await orderProductChange(
        account.id,
        product.id,
        change,
        day,
        context.timeZone,
      );
      if (result.state === 'completed') {
        onDone(`${name}: ${TITLE[change].toLowerCase()} recorded for ${day}`);
      } else if (result.state === 'inProgress') {
        onDone(
          `${name}: ${TITLE[change].toLowerCase()} accepted, the billing worker completes it`,
          {
            id: result.id,
            label: `${TITLE[change]} of ${name}`,
          },
        );
      } else {
        setProblem(`Rejected: ${result.reasons.join(' ') || result.state}`);
      }
    } catch (error) {
      if (uncertain(error)) {
        setUnsure(true);
        setProblem(`${explain(error)}. Unsure whether it was saved: Refresh to check.`);
      } else {
        setProblem(explain(error));
      }
    } finally {
      saving.current = false;
      setBusy(false);
    }
  }

  return (
    <Modal
      title={`${TITLE[change]}: ${name}`}
      width={520}
      onClose={() => {
        if (!saving.current) onClose();
      }}
      ui="ui.subscriber.productChange"
      footer={
        <>
          {problem !== null && (
            <div className="bss-inline-error" role="alert" style={{ marginRight: 'auto' }}>
              {problem}
            </div>
          )}
          {unsure && (
            <Button
              onClick={() => {
                onClose();
                onRefresh();
              }}
            >
              Refresh
            </Button>
          )}
          <Button disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!valid || busy} onClick={() => void submit()}>
            {TITLE[change]}
          </Button>
        </>
      }
    >
      <div className="bss-modal-body">
        <section>
          <div className="bss-step-head">{change === 'end' ? 'End on' : 'Date'}</div>
          <TextInput
            mono
            width={180}
            aria-label="Date"
            placeholder="YYYY-MM-DD"
            value={day}
            readOnly={fixed}
            onChange={(event) => setDay(event.target.value)}
          />
          {!valid && <div className="bss-inline-error">Enter a date as YYYY-MM-DD.</div>}
        </section>
        <p className="bss-secondary">{explanation}</p>
      </div>
    </Modal>
  );
}
