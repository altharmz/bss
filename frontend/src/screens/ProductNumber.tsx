import { Footer } from './FinancialForms';
import { orderRefusal } from '../api/orderErrors';
import { submitDialogPrimary } from '../ui/kit';
import { useState } from 'react';
import type { components as T637 } from '../api/generated/tmf637';
import { getJson, explain } from '../api/client';
import type { PendingOrder } from '../api/commands';
import { PATHS } from '../api/bss';
import {
  changeNumber,
  iccidOf,
  kindOf,
  loadNumbersById,
  requirements,
  type ResourceKind,
} from '../api/numbers';
import { useResource } from '../api/useResource';
import { Button, Dialog } from '../ui/kit';
import { ErrorBlock } from '../ui/states';
import { NumberPicker } from './Numbers';
import { SimActivationCode } from './SimActivationCode';
import { simState, simType } from '../api/sims';
import { AccountPinField } from './SecurityTab';

export function ProductNumber({
  accountId,
  productId,
  onDone,
}: {
  accountId: string;
  productId: string;
  onDone: (message: string, accepted?: PendingOrder) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button small onClick={() => setOpen(true)}>
        Subscriber number
      </Button>
      {open && (
        <NumberModal
          accountId={accountId}
          productId={productId}
          onClose={() => setOpen(false)}
          onDone={onDone}
        />
      )}
    </>
  );
}

export function NumberModal({
  accountId,
  productId,
  onClose,
  onDone,
}: {
  accountId: string;
  productId: string;
  onClose: () => void;
  onDone: (message: string, accepted?: PendingOrder) => void;
}) {
  const product = useResource(`number-product:${productId}`, (signal) =>
    getJson<T637['schemas']['Product']>(`${PATHS.products}/${productId}`, signal),
  );
  const [numberId, setNumberId] = useState('');
  const [imsiId, setImsiId] = useState('');
  const [accountPin, setAccountPin] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [code, setCode] = useState<string | undefined>();
  const [uncertain, setUncertain] = useState(false);
  const refs = product.status === 'ok' ? (product.data.realizingResource ?? []) : [];
  // the product's own numbers, to tell its MSISDN from its IMSI (#314: not the inventory)
  const refIds = refs.map((r) => r.id ?? '').filter(Boolean);
  const inventory = useResource(`product-number-refs:${refIds.join(',')}`, (signal) =>
    loadNumbersById(refIds, signal),
  );
  const known = inventory.status === 'ok' ? inventory.data : [];
  const kindOfRef = (id?: string): ResourceKind | undefined => {
    const n = known.find((k) => k.id === id);
    return n ? kindOf(n) : undefined;
  };
  const currentImsi = refs.find((r) => kindOfRef(r.id) === 'imsi');
  const currentMsisdn = refs.find((r) => kindOfRef(r.id) !== 'imsi');
  const simDetail = known.find((k) => k.id === currentImsi?.id);
  const specificationId =
    product.status === 'ok' ? product.data.productSpecification?.id : undefined;
  const requirement = useResource(
    `product-number-requirement:${specificationId ?? ''}`,
    (signal) =>
      specificationId
        ? requirements(specificationId, signal)
        : Promise.resolve({ msisdn: false, imsi: false }),
  );
  const startedOk =
    product.status === 'ok' &&
    ['active', 'created', 'pendingActive', 'suspended'].includes(product.data.status ?? '');
  const needs = requirement.status === 'ok' ? requirement.data : { msisdn: false, imsi: false };
  const editable =
    needs.msisdn && startedOk && refs.length === (needs.imsi ? 2 : 1) && inventory.status === 'ok';
  const simEditable =
    needs.imsi && startedOk && currentImsi !== undefined && inventory.status === 'ok';
  async function submit(change: ResourceKind) {
    // the complete resulting set: the changed kind's new resource, the other kind unchanged
    const ids = [
      change === 'msisdn' ? numberId : currentMsisdn?.id,
      needs.imsi ? (change === 'imsi' ? imsiId : currentImsi?.id) : undefined,
    ].filter((id): id is string => Boolean(id));
    setBusy(true);
    setProblem('');
    setCode(undefined);
    try {
      const result = await changeNumber(accountId, productId, ids, accountPin);
      if (result.state === 'completed') {
        onClose();
        onDone(
          change === 'imsi'
            ? 'SIM changed through an order'
            : 'Subscriber number changed through an order',
        );
      } else if (result.state === 'inProgress') {
        onClose();
        onDone(
          change === 'imsi'
            ? 'SIM swap accepted: the network confirms it, then the billing worker completes it'
            : 'Subscriber number change accepted, the billing worker completes it shortly',
          {
            id: result.id,
            label: change === 'imsi' ? 'SIM swap' : 'Subscriber number change',
          },
        );
      } else {
        const refusal = orderRefusal(result.productOrderErrorMessage ?? [], result.state);
        setProblem(refusal.problem);
        setCode(refusal.code);
      }
    } catch (error) {
      setProblem(`${explain(error)}. Check product and order history before submitting again.`);
      setUncertain(true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title="Subscriber number"
      size="M"
      onClose={onClose}
      ui="ui.subscriber.number"
      footer={
        <Footer
          write={{ busy, problem: problem || null, unsure: uncertain, code }}
          onRefresh={onClose}
        >
          <Button onClick={onClose}>Close</Button>
          {editable && (
            <Button
              data-dialog-primary
              disabled={busy || uncertain || !numberId}
              onClick={() => void submit('msisdn')}
            >
              Change number
            </Button>
          )}
          {simEditable && (
            <Button disabled={busy || uncertain || !imsiId} onClick={() => void submit('imsi')}>
              Change SIM
            </Button>
          )}
        </Footer>
      }
    >
      <fieldset disabled={busy} className="bss-write-fields bss-modal-body">
        {product.status === 'loading' && <p>Loading number…</p>}
        {product.status === 'error' && (
          <ErrorBlock
            title="Failed to load product"
            error={product.error}
            onRetry={product.retry}
          />
        )}
        {product.status === 'ok' && (
          <p>
            {refs.map((r) => r.name ?? r.id).join(', ') || 'This product has no subscriber number.'}
          </p>
        )}
        {simDetail && iccidOf(simDetail) && (
          <p>
            ICCID {iccidOf(simDetail)}
            {simType(simDetail) === 'esim' ? ' · eSIM' : ''}
            {simState(simDetail) ? ` · ${simState(simDetail)?.replace('_', ' ')}` : ''}
          </p>
        )}
        {simDetail && <SimActivationCode sim={{ ...simDetail, id: simDetail.id }} />}
        {requirement.status === 'error' && (
          <ErrorBlock
            title="Failed to load number requirement"
            error={requirement.error}
            onRetry={requirement.retry}
          />
        )}
        {product.status === 'ok' &&
          requirement.status === 'ok' &&
          !requirement.data.msisdn &&
          !requirement.data.imsi && (
            <p>
              This product's offering does not use subscriber numbers. Offerings whose specification
              requires a number ask for one when the product is added.
            </p>
          )}
        {simEditable && (
          <>
            <NumberPicker kind="imsi" value={imsiId} onChange={setImsiId} />
            <p>
              The SIM is swapped through a modify order that waits for the network to confirm. Until
              then the line keeps its current SIM; if the network refuses, nothing changes. The
              previous SIM is retired, not reused. No price change.
            </p>
          </>
        )}
        {editable && (
          <>
            <NumberPicker value={numberId} onChange={setNumberId} />
            <p>
              Changes take effect today through a modify order. The previous number is released
              subject to quarantine. No price change.
            </p>
          </>
        )}
        {(editable || simEditable) && (
          <AccountPinField value={accountPin} onChange={setAccountPin} />
        )}
      </fieldset>
    </Dialog>
  );
}
