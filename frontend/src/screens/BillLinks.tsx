import { submitDialogPrimary } from '../ui/kit';
import { useState } from 'react';
import { loadBillCycle, loadRate, loadRates, type RateRow } from '../api/bills';
import { useResource } from '../api/useResource';
import { useSystemContext } from '../app/context';
import { businessDate } from '../format/dates';
import { shortId } from '../format/ids';
import { formatAmount } from '../format/money';
import { Button, Modal } from '../ui/kit';
import { ErrorBlock, ListState, SkeletonRows } from '../ui/states';
import { TableHead, TableRow, type Column } from '../ui/Table';

// What a bill links to (docs/ui.md): its bill cycle (the daily
// billing run that issued it) and the applied rates on it. Both load only when opened, so
// the bill dialog itself makes no extra request.

const RATE_COLUMNS: Column[] = [
  { key: 'period', label: 'Period', width: '190px', mono: true },
  { key: 'product', label: 'Product', width: 'minmax(0, 1fr)' },
  { key: 'amount', label: 'Amount', width: '110px', align: 'right', mono: true },
  { key: 'tax', label: 'Tax', width: '100px', align: 'right', mono: true },
];

function show(value: unknown): string {
  return typeof value === 'object' && value !== null ? JSON.stringify(value) : String(value);
}

/** The raw fields of a GET, one line each. */
function Fields({ body }: { body: Record<string, unknown> }) {
  return (
    <dl className="bss-caption">
      {Object.entries(body).map(([key, value]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd className="num">{show(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function DetailDialog({
  title,
  ui,
  detail,
  onClose,
}: {
  title: string;
  ui: string;
  detail: ReturnType<typeof useResource<Record<string, unknown>>>;
  onClose: () => void;
}) {
  return (
    <Modal
      onSubmit={submitDialogPrimary}
      title={title}
      width={640}
      onClose={onClose}
      ui={ui}
      footer={<Button onClick={onClose}>Close</Button>}
    >
      <div className="bss-modal-body">
        {detail.status === 'loading' && <SkeletonRows columns={RATE_COLUMNS} rows={3} />}
        {detail.status === 'error' && (
          <ErrorBlock title="Failed to load" error={detail.error} onRetry={detail.retry} />
        )}
        {detail.status === 'ok' && <Fields body={detail.data} />}
      </div>
    </Modal>
  );
}

/** One bill cycle from GET billCycle/{id}. */
export function CycleDialog({ cycleId, onClose }: { cycleId: string; onClose: () => void }) {
  const detail = useResource(`billCycle:${cycleId}`, (signal) => loadBillCycle(cycleId, signal));
  return (
    <DetailDialog
      title={`Bill cycle ${shortId(cycleId)}`}
      ui="ui.bills.cycleDetail"
      detail={detail}
      onClose={onClose}
    />
  );
}

function RateDialog({ rateId, onClose }: { rateId: string; onClose: () => void }) {
  const detail = useResource(`rate:${rateId}`, (signal) => loadRate(rateId, signal));
  return (
    <DetailDialog
      title={`Rate ${shortId(rateId)}`}
      ui="ui.bills.rateDetail"
      detail={detail}
      onClose={onClose}
    />
  );
}

function Rates({ billId }: { billId: string }) {
  const { timeZone } = useSystemContext();
  const [open, setOpen] = useState<string | null>(null);
  const rates = useResource(`rates:${billId}`, (signal) => loadRates(billId, signal));
  const day = (instant?: string) => (instant === undefined ? '' : businessDate(instant, timeZone));
  const taxOf = (rate: RateRow) =>
    (rate.appliedTax ?? []).reduce((sum, t) => sum + (t.taxAmount?.value ?? 0), 0);
  return (
    <>
      <TableHead columns={RATE_COLUMNS} />
      <ListState
        resource={rates}
        columns={RATE_COLUMNS}
        errorTitle="Failed to load rates"
        empty={{ title: 'No rates', text: 'This bill has no applied rates.' }}
        total={(data) => data.length}
        rows={(data) => data}
        onResetFilters={() => {}}
      >
        {(rows) =>
          rows.map((rate) => (
            <TableRow
              key={rate.id}
              columns={RATE_COLUMNS}
              onClick={() => setOpen(rate.id)}
              cells={{
                period: `${day(rate.periodCoverage?.startDateTime)} – ${day(
                  rate.periodCoverage?.endDateTime,
                )}`,
                product: rate.product?.name ?? rate.name ?? '—',
                amount: rate.taxExcludedAmount ? formatAmount(rate.taxExcludedAmount.value) : '—',
                tax: formatAmount(taxOf(rate)),
              }}
            />
          ))
        }
      </ListState>
      {open !== null && <RateDialog rateId={open} onClose={() => setOpen(null)} />}
    </>
  );
}

/** The links under a bill: Bill cycle (when the bill has one) and Rates. */
export function BillLinks({ billId, cycleId }: { billId: string; cycleId?: string | undefined }) {
  const [cycle, setCycle] = useState(false);
  const [rates, setRates] = useState(false);
  return (
    <section data-ui="ui.bills.links">
      <div className="bss-step-head">Related</div>
      <div className="bss-bill-actions">
        {cycleId !== undefined && (
          <Button small onClick={() => setCycle(true)}>
            Bill cycle
          </Button>
        )}
        <Button small aria-pressed={rates} onClick={() => setRates(!rates)}>
          {rates ? 'Hide rates' : 'Rates'}
        </Button>
      </div>
      {rates && <Rates billId={billId} />}
      {cycle && cycleId !== undefined && (
        <CycleDialog cycleId={cycleId} onClose={() => setCycle(false)} />
      )}
    </section>
  );
}
