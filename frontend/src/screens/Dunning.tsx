import { useState } from 'react';
import {
  DUNNING_PAGE,
  LAST_STEPS,
  loadAccountDunning,
  loadDunningAccounts,
  setDunningExemption,
  STEP_LABEL,
  type AccountDunning,
  type LastStep,
} from '../api/dunning';
import { useResource } from '../api/useResource';
import { useSetCrumbs } from '../app/context';
import { hrefOf, Link } from '../app/router';
import { plural } from '../format/labels';
import { toCents } from '../format/money';
import { Amount } from '../ui/Amount';
import { Id } from '../ui/Id';
import {
  Button,
  Checkbox,
  Dialog,
  Field,
  PageHeader,
  Panel,
  Segmented,
  Tag,
  TextInput,
  submitDialogPrimary,
  useToast,
} from '../ui/kit';
import { Note } from '../ui/Note';
import { PageFooter, usePageOffset } from '../ui/paging';
import { ErrorBlock, ListState, SkeletonRows } from '../ui/states';
import { TableHead, TableRow, type Column } from '../ui/Table';
import { Footer, useWrite } from './FinancialForms';
import './screens.css';
import { AccountFilter, useAccountFilter } from './AccountFilter';

// Postpaid dunning (#386, docs/decisions/dunning.md): the Dunning screen under Billing (the
// accounts in dunning with a step filter, and the deactivation candidates) and the subscriber
// card's dunning panel with the exemption. The server computes O, the steps and the next step.

const COLUMNS: Column[] = [
  { key: 'account', label: 'Subscriber', width: 'minmax(0, 1fr)' },
  { key: 'since', label: 'Overdue since', width: '120px', mono: true },
  { key: 'amount', label: 'Past due', width: '130px', align: 'right', mono: true },
  { key: 'step', label: 'Last step', width: '150px' },
  { key: 'suspended', label: 'Suspended since', width: '130px', mono: true },
];

type Tab = 'dunning' | 'candidates';
type StepFilter = LastStep | 'all';

export function DunningScreen() {
  useSetCrumbs([{ label: 'Dunning' }], 'dunning');
  const [tab, setTab] = useState<Tab>('dunning');
  const [step, setStep] = useState<StepFilter>('all');
  const accountFilter = useAccountFilter('/dunning');
  const { account } = accountFilter;
  const filter = {
    step: tab === 'dunning' && step !== 'all' ? step : null,
    candidates: tab === 'candidates',
    account,
  };
  const filterKey = `${tab}:${filter.step ?? ''}:${account ?? ''}`;
  const [offset, setOffset] = usePageOffset(filterKey);
  const accounts = useResource(`dunning:${filterKey}:${offset}`, (signal) =>
    loadDunningAccounts(filter, offset, signal),
  );
  const name =
    accounts.status === 'ok'
      ? (accounts.data.items.find((r) => r.billingAccount.id === account)?.billingAccount.name ??
        null)
      : null;
  const reset = () => {
    setStep('all');
    if (account !== null) accountFilter.drop();
  };
  return (
    <div className="bss-screen" data-ui="ui.dunning">
      <div className="bss-title-row">
        <PageHeader title="Dunning" />
        <span className="bss-caption">Postpaid accounts with an amount past due, oldest first</span>
      </div>
      <div className="bss-toolbar">
        <Segmented
          label="Dunning lists"
          value={tab}
          onChange={setTab}
          options={[
            { key: 'dunning', label: 'In dunning' },
            { key: 'candidates', label: 'Deactivation candidates' },
          ]}
        />
        {tab === 'dunning' && (
          <Segmented
            label="Last step"
            value={step}
            onChange={setStep}
            options={[
              { key: 'all', label: 'All' },
              ...LAST_STEPS.map((s) => ({ key: s, label: STEP_LABEL[s] })),
            ]}
          />
        )}
        <AccountFilter ui="ui.dunning" filter={accountFilter} name={name} />
      </div>
      <Panel>
        {tab === 'candidates' && (
          <Note tone="info">
            Suspended for more than 60 days. Postpaid lines are never ended automatically: end them
            from the subscriber card.
          </Note>
        )}
        <TableHead columns={COLUMNS} />
        <ListState
          resource={accounts}
          columns={COLUMNS}
          errorTitle="Failed to load the accounts in dunning"
          empty={
            tab === 'dunning'
              ? {
                  title: 'No accounts in dunning',
                  text: 'An account enters dunning when it owes the minimum past due.',
                }
              : {
                  title: 'No deactivation candidates',
                  text: 'An account becomes one after 60 days of dunning suspension.',
                }
          }
          // filtered on the server: an empty filtered page is "no matches"
          count={(data) => (filter.step !== null || account !== null ? 1 : data.items.length)}
          rows={(data) => data.items}
          onResetFilters={reset}
        >
          {(rows, data) => (
            <>
              {rows.map((row) => (
                <TableRow
                  key={row.billingAccount.id}
                  columns={COLUMNS}
                  cells={{
                    account: (
                      <span className="ellipsis">
                        <Link
                          className="bss-link"
                          href={`${hrefOf({
                            name: 'subscriber',
                            accountId: row.billingAccount.id,
                          })}#bills`}
                        >
                          {row.billingAccount.name}
                        </Link>{' '}
                        <Id id={row.billingAccount.id} prefix="BA-" />
                      </span>
                    ),
                    since: row.overdueSince,
                    amount: (
                      <Amount
                        cents={toCents(row.overdueAmount.value)}
                        currency={row.overdueAmount.unit}
                      />
                    ),
                    step: (
                      <span>
                        {STEP_LABEL[row.lastStep]}
                        {row.deactivationCandidate && tab === 'dunning' && (
                          <>
                            {' '}
                            <Tag>Candidate</Tag>
                          </>
                        )}
                      </span>
                    ),
                    suspended: row.suspendedSince ?? '—',
                  }}
                />
              ))}
              <PageFooter
                offset={data.offset}
                shown={rows.length}
                more={data.more}
                size={DUNNING_PAGE}
                onOffset={setOffset}
                ui="ui.dunning"
              />
            </>
          )}
        </ListState>
      </Panel>
    </div>
  );
}

/** The subscriber card's dunning panel (postpaid accounts): O, steps taken, next step and
 * the exemption toggle with a confirmation. */
export function SubscriberDunningPanel({
  accountId,
  version,
}: {
  accountId: string;
  version: number;
}) {
  const [changes, setChanges] = useState(0);
  const dunning = useResource(`dunning:${accountId}:${version}:${changes}`, (signal) =>
    loadAccountDunning(accountId, signal),
  );
  const [confirming, setConfirming] = useState(false);
  const toast = useToast();
  return (
    <section data-ui="ui.subscriber.dunning">
      <Panel title="Dunning">
        {dunning.status === 'loading' && (
          <SkeletonRows
            columns={[
              { key: 'what', label: 'What', width: '1fr' },
              { key: 'when', label: 'When', width: '1fr' },
            ]}
            rows={2}
          />
        )}
        {dunning.status === 'error' && (
          <ErrorBlock
            title="Failed to load the dunning state"
            error={dunning.error}
            onRetry={dunning.retry}
          />
        )}
        {dunning.status === 'ok' && (
          <>
            <DunningState state={dunning.data} />
            <Checkbox
              data-ui="ui.subscriber.dunning.exempt"
              label="Exempt from dunning"
              checked={dunning.data.exempt}
              // the checkbox stays as the server says until the confirmation is saved
              onChange={() => setConfirming(true)}
            />
          </>
        )}
      </Panel>
      {confirming && dunning.status === 'ok' && (
        <ExemptionDialog
          accountId={accountId}
          exempt={!dunning.data.exempt}
          onClose={() => setConfirming(false)}
          onDone={(message) => {
            setConfirming(false);
            toast(message);
            setChanges((n) => n + 1);
          }}
        />
      )}
    </section>
  );
}

function DunningState({ state }: { state: AccountDunning }) {
  const suspended = state.suspendedProducts.length;
  return (
    <div>
      {state.exempt && (
        <div className="bss-line">
          <Tag>Exempt</Tag>
          <span className="bss-secondary">No dunning step is taken for this account.</span>
        </div>
      )}
      {state.inDunning && state.overdueSince !== null && state.overdueAmount !== null ? (
        <>
          <div className="bss-line">
            <span>In dunning since {state.overdueSince}</span>
            <Amount
              cents={toCents(state.overdueAmount.value)}
              currency={state.overdueAmount.unit}
            />
          </div>
          <div className="bss-step-head">Steps taken</div>
          {state.stepsTaken.length === 0 && <div className="bss-secondary">None yet.</div>}
          {state.stepsTaken.map((s) => (
            <div key={s.step} className="bss-line">
              <span>{STEP_LABEL[s.step]}</span>
              <span className="bss-secondary num">{s.date}</span>
            </div>
          ))}
          <div className="bss-step-head">Next step</div>
          {state.nextStep === null ? (
            <div className="bss-secondary">None: every step is taken.</div>
          ) : (
            <div className="bss-line">
              <span>{STEP_LABEL[state.nextStep.step]}</span>
              <span className="bss-secondary num">{state.nextStep.date}</span>
            </div>
          )}
        </>
      ) : (
        <div className="bss-secondary">Not in dunning.</div>
      )}
      {suspended > 0 && (
        <div className="bss-secondary">{plural(suspended, 'line')} suspended by dunning.</div>
      )}
    </div>
  );
}

function ExemptionDialog({
  accountId,
  exempt,
  onClose,
  onDone,
}: {
  accountId: string;
  exempt: boolean;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const [reason, setReason] = useState('');
  const write = useWrite(onDone);
  const title = exempt ? 'Exempt from dunning?' : 'End the dunning exemption?';
  return (
    <Dialog
      title={title}
      size="S"
      ui="ui.subscriber.dunning.exemptConfirm"
      onClose={onClose}
      onSubmit={submitDialogPrimary}
      footer={
        <Footer write={write} onRefresh={() => onDone('Dunning state refreshed')}>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            busyLabel="Saving…"
            disabled={write.busy || reason.length > 500}
            onClick={() =>
              void write.run(async () => {
                await setDunningExemption(accountId, exempt, reason);
                return exempt ? 'Exempt from dunning' : 'Dunning exemption ended';
              })
            }
          >
            {exempt ? 'Exempt' : 'End exemption'}
          </Button>
        </Footer>
      }
    >
      <div className="bss-modal-body">
        <p>
          {exempt
            ? 'The account takes no further dunning step, and lines suspended by dunning are restored on the next pass.'
            : 'Dunning applies again from the next pass: reminders and suspension follow the settings.'}
        </p>
        <fieldset className="bss-form bss-write-fields" disabled={write.busy || write.unsure}>
          <Field label="Reason (optional)" help="Recorded in the account audit">
            <TextInput
              aria-label="Reason"
              maxLength={500}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </Field>
        </fieldset>
      </div>
    </Dialog>
  );
}
