import { submitDialogPrimary } from '../ui/kit';
import { useState } from 'react';
import { loadBillCycle, loadRate, loadRates, type RateRow } from '../api/bills';
import { useResource } from '../api/useResource';
import { useSystemContext } from '../app/context';
import { businessDate } from '../format/dates';
import { shortId } from '../format/ids';
import { formatAmount } from '../format/money';
import { Button, Dialog } from '../ui/kit';
import { ErrorBlock, ListState, SkeletonRows } from '../ui/states';
import { TableHead, TableRow, type Column } from '../ui/Table';
import { MultiLineDiscount, rateDiscount } from './MultiLine';
import { TaxLines, taxLinesOf } from './TaxLines';

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

/** A rate's multi-line discount, from its description (TMF678 has no price alteration). */
function RateDiscount({ body }: { body: Record<string, unknown> }) {
  const text = rateDiscount(typeof body.description === 'string' ? body.description : null);
  return text === null ? null : (
    <p>
      Discount: <MultiLineDiscount text={text} ui="ui.bills.rateDetail.multiLineDiscount" />
    </p>
  );
}

function DetailDialog({
  title,
  ui,
  detail,
  onClose,
  taxes = false,
}: {
  title: string;
  ui: string;
  detail: ReturnType<typeof useResource<Record<string, unknown>>>;
  onClose: () => void;
  /** a rate: its appliedTax lines as a table above the fields */
  taxes?: boolean;
}) {
  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title={title}
      size="L"
      onClose={onClose}
      ui={ui}
      footer={<Button onClick={onClose}>Close</Button>}
    >
      <div className="bss-modal-body">
        {detail.status === 'loading' && <SkeletonRows columns={RATE_COLUMNS} rows={3} />}
        {detail.status === 'error' && (
          <ErrorBlock title="Failed to load" error={detail.error} onRetry={detail.retry} />
        )}
        {detail.status === 'ok' && taxes && <RateDiscount body={detail.data} />}
        {detail.status === 'ok' && taxes && <TaxLines lines={taxLinesOf(detail.data.appliedTax)} />}
        {detail.status === 'ok' && <Fields body={detail.data} />}
      </div>
    </Dialog>
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
      taxes
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
        count={(data) => data.length}
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
                product: (() => {
                  const discount = rateDiscount(rate.description);
                  const name = rate.product?.name ?? rate.name ?? '—';
                  return discount === null ? (
                    name
                  ) : (
                    <>
                      {name} <MultiLineDiscount text={discount} ui="ui.bills.multiLineDiscount" />
                    </>
                  );
                })(),
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
