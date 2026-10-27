import { Footer } from './FinancialForms';
import { orderRefusal } from '../api/orderErrors';
import { submitDialogPrimary } from '../ui/kit';
import { useState } from 'react';
import type { components as T637 } from '../api/generated/tmf637';
import { getJson, explain } from '../api/client';
import type { PendingOrder } from '../api/commands';
import { PATHS } from '../api/bss';
import { changeNumber, requiresNumber } from '../api/numbers';
import { useResource } from '../api/useResource';
import { Button, Modal } from '../ui/kit';
import { ErrorBlock } from '../ui/states';
import { NumberPicker } from './Numbers';

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

function NumberModal({
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
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [code, setCode] = useState<string | undefined>();
  const [uncertain, setUncertain] = useState(false);
  const refs = product.status === 'ok' ? (product.data.realizingResource ?? []) : [];
  const specificationId =
    product.status === 'ok' ? product.data.productSpecification?.id : undefined;
  const requirement = useResource(
    `product-number-requirement:${specificationId ?? ''}`,
    (signal) =>
      specificationId ? requiresNumber(specificationId, signal) : Promise.resolve(false),
  );
  const editable =
    requirement.status === 'ok' &&
    requirement.data &&
    refs.length === 1 &&
    product.status === 'ok' &&
    ['active', 'created', 'pendingActive', 'suspended'].includes(product.data.status ?? '');
  async function submit() {
    setBusy(true);
    setProblem('');
    setCode(undefined);
    try {
      const result = await changeNumber(accountId, productId, numberId);
      if (result.state === 'completed') {
        onClose();
        onDone('Subscriber number changed through an order');
      } else if (result.state === 'inProgress') {
        onClose();
        onDone('Subscriber number change accepted, the billing worker completes it shortly', {
          id: result.id,
          label: 'Subscriber number change',
        });
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
    <Modal
      onSubmit={submitDialogPrimary}
      title="Subscriber number"
      width={600}
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
              onClick={() => void submit()}
            >
              Change number
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
        {requirement.status === 'error' && (
          <ErrorBlock
            title="Failed to load number requirement"
            error={requirement.error}
            onRetry={requirement.retry}
          />
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
      </fieldset>
    </Modal>
  );
}
