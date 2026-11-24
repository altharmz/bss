import { TableFooter, TableHead, TableRow } from '../ui/Table';
import { EmptyBlock, ErrorBlock, NoResults, SkeletonRows } from '../ui/states';
import { Amount } from '../ui/Amount';
import { Combobox } from '../ui/combobox';
import { Id, ID_PREFIXES } from '../ui/Id';
import { STATUS_MAP, type StatusKind } from '../ui/status';
import { useState } from 'react';
import {
  Avatar,
  Icon,
  IconButton,
  ICON_NAMES,
  Radio,
  Switch,
  Menu,
  Note,
  Tabs,
  TabPanel,
  Badge,
  Flag,
  StatusPill,
  Tag,
  Button,
  Checkbox,
  DateField,
  Dialog,
  Field,
  MoneyField,
  KeyValue,
  MoneyStrip,
  PageHeader,
  Panel,
  Segmented,
  SelectField,
  TextInput,
  ToastMessage,
  useToast,
} from '../ui/kit';
import './screens.css';
import { Footer } from './FinancialForms';

const COMBOBOX_OPTIONS = [
  { value: 'p1', label: 'Olga Kuznetsova · person' },
  { value: 'o1', label: 'Romashka LLC · organization' },
  { value: 'o2', label: 'Lone Ltd · organization' },
];

/** Ledger Components artboard: live controls without screen-specific requests. */
export function UiKitScreen() {
  const toast = useToast();
  const [tab, setTab] = useState('overview');
  const [mode, setMode] = useState('prepaid');
  const [money, setMoney] = useState('42.10');
  const [owner, setOwner] = useState('');
  const [chosen, setChosen] = useState('o1');
  return (
    <div className="ui-kit" data-ui="ui.kit">
      <h1>UI kit</h1>
      <section>
        <h2>Dialog write states</h2>
        <div className="ui-kit-write-states">
          {(['idle', 'busy', 'refused', 'unsure'] as const).map((state) => (
            <div key={state}>
              <p className="ui-kit-write-label">{state}</p>
              <Dialog
                preview
                size="M"
                title="Record payment"
                context="Ivan Petrov · BA-3F9A12C0"
                onClose={() => {}}
                footer={
                  <Footer
                    write={{
                      busy: state === 'busy',
                      unsure: state === 'unsure',
                      problem:
                        state === 'refused'
                          ? "A single payment can't exceed 10,000.00 EUR. Check the amount and try again."
                          : null,
                      code: state === 'refused' ? 'PAYMENT_LIMIT' : undefined,
                      failedCall: state === 'unsure' ? 'POST /payment → Network error' : undefined,
                    }}
                    onRefresh={() => {}}
                  >
                    <Button variant="quiet">Cancel</Button>
                    <Button variant="primary" busyLabel="Recording…">
                      Record {state === 'refused' ? '42,100.00' : '42.10'} EUR
                    </Button>
                  </Footer>
                }
              >
                <div className="ui-kit-write-balance">
                  <span>
                    Balance <Amount cents={-4210} />
                  </span>
                  <span>
                    Balance after <Amount cents={state === 'refused' ? 4205790 : 0} />
                  </span>
                </div>
                <Field label="Amount received">
                  <MoneyField
                    currency="EUR"
                    defaultValue={state === 'refused' ? '42100.00' : '42.10'}
                    disabled={state === 'busy'}
                    readOnly={state === 'unsure'}
                  />
                </Field>
              </Dialog>
            </div>
          ))}
        </div>
      </section>
      <section>
        <h2>Dialogs</h2>
        {(['S', 'M', 'L'] as const).map((size) => (
          <Dialog
            key={size}
            preview
            size={size}
            title={`Dialog ${size}`}
            context="Ivan Petrov · BA-3F9A12C0"
            step={{ n: 1, of: 3 }}
            onClose={() => {}}
            footer={
              <>
                <span className="footer-push">Ready to continue.</span>
                <Button variant="quiet">Cancel</Button>
                <Button variant="primary">Continue</Button>
              </>
            }
          >
            <Field label="Amount received">
              <MoneyField currency="EUR" defaultValue="42.10" />
            </Field>
          </Dialog>
        ))}
      </section>
      <section>
        <h2>Success toast</h2>
        <ToastMessage
          preview
          message="Payment recorded · 120.00 EUR"
          action={{ label: 'View', onClick: () => toast('Payment opened') }}
        />
        <Button
          onClick={() =>
            toast('Payment recorded · 120.00 EUR', {
              label: 'View',
              onClick: () => toast('Payment opened'),
            })
          }
        >
          Show toast
        </Button>
      </section>
      <section>
        <h2>Page header and summaries</h2>
        <PageHeader
          title="Subscribers"
          count="1,284"
          meta="Billing accounts"
          actions={<Button variant="primary">New subscriber</Button>}
          filters={
            <>
              <label className="sr-only" htmlFor="ui-kit-search">
                Search subscribers
              </label>
              <TextInput id="ui-kit-search" placeholder="Name, BA id or number" />
            </>
          }
        />
        <MoneyStrip
          figures={[
            { label: 'Ledger', value: <Amount cents={1850} currency="EUR" />, emphasis: 'figure' },
            { label: 'Reserved', value: <Amount cents={500} />, emphasis: 'figure' },
            {
              label: 'Available',
              value: <Amount cents={1350} currency="EUR" />,
              emphasis: 'answer',
            },
            { label: 'Paid through', value: '2026-11-14', sub: 'in 19 days' },
            { label: 'Shortfall', value: '—', sub: 'none', tone: 'muted' },
          ]}
          operators={['−', '=']}
          divider={[3]}
          actions={
            <>
              <Button variant="primary">Top up</Button>
              <Button>Adjust</Button>
              <Button>Hold</Button>
              <Button>Transfer</Button>
            </>
          }
        />
        <MoneyStrip
          figures={[
            {
              label: 'Balance',
              value: <Amount cents={-125040} currency="EUR" />,
              emphasis: 'answer',
              tone: 'negative',
            },
            {
              label: 'Credit limit',
              value: <Amount cents={50000} />,
              sub: <Flag>Over by 750.40</Flag>,
            },
            { label: 'Cycle day', value: '1', sub: 'next bill 2026-11-01' },
          ]}
          divider={[1]}
          actions={<Button variant="primary">Record payment</Button>}
        />
        <KeyValue
          items={[
            ['Account', <Id prefix="BA-" id="81d0e7b4-0000-4000-8000-000000000001" />],
            ['Party', 'Nordwind Logistics OÜ'],
            ['Customer since', '2023-05-02'],
          ]}
        />
        <Panel
          title="Order in progress"
          subtitle="ORD-26-10-00418"
          right={<Button small>Open order</Button>}
        >
          <Note tone="info">Panels group work the operator acts on as one.</Note>
        </Panel>
        <Panel>
          <Note tone="info">A panel without a title is only a box.</Note>
        </Panel>
      </section>
      <section data-ui="ui.uikit.icons">
        <h2>Icons</h2>
        <div className="ui-kit-icons">
          {ICON_NAMES.map((name) => (
            <span key={name} className="ui-kit-icon">
              <Icon name={name} />
              <code>{name}</code>
            </span>
          ))}
        </div>
      </section>
      <section>
        <h2>Inline notes</h2>
        <Note tone="info" lead="Change scheduled.">
          Mobile S becomes Mobile L on 2026-11-15; the price changes from that date.
        </Note>
        <Note tone="warning" lead="Shortfall 2.56 EUR.">
          The first charge needs 16.06; 13.50 is available. Top up first.
        </Note>
        <Note tone="negative" lead="Refused by the server." code="INSUFFICIENT_FUNDS">
          Amount exceeds the available balance (13.50 EUR).
        </Note>
      </section>
      <section className="ui-kit-navigation">
        <h2>Tabs and menu</h2>
        <Tabs
          id="kit"
          label="Account sections"
          value={tab}
          onChange={setTab}
          options={[
            { key: 'overview', label: 'Overview' },
            { key: 'products', label: 'Products', count: 2 },
            { key: 'orders', label: 'Orders', badge: <Badge kind="info">1 open</Badge> },
          ]}
        />
        <TabPanel id="kit" value={tab}>
          {tab}
        </TabPanel>
        <Menu
          defaultOpen
          label="Product actions"
          items={[
            { label: 'Suspend', onClick: () => {} },
            { label: 'Resume', disabled: true, onClick: () => {} },
            { label: 'Change offering…', onClick: () => {} },
            { separator: true },
            { label: 'Terminate…', tone: 'negative', onClick: () => {} },
          ]}
        />
        <TableFooter
          range={{ from: 1, to: 50, total: 1284 }}
          right={
            <>
              <Button small disabled>
                Previous
              </Button>{' '}
              <Button small>Next</Button>
            </>
          }
        />
      </section>
      <section>
        <h2>Tables and list states</h2>
        {(['default', 'compact', 'dense'] as const).map((density) => (
          <div key={density} role="table" aria-label={`${density} table`}>
            <TableHead
              columns={[
                { key: 'name', label: 'Name', width: '1fr' },
                { key: 'amount', label: 'Balance EUR', width: '140px', align: 'right' },
              ]}
              density={density}
            />
            <TableRow
              columns={[
                { key: 'name', label: 'Name', width: '1fr' },
                { key: 'amount', label: 'Balance EUR', width: '140px', align: 'right' },
              ]}
              density={density}
              selected
              cells={{ name: 'Selected subscriber', amount: <Amount cents={4210} /> }}
              onClick={() => {}}
            />
          </div>
        ))}
        <SkeletonRows
          columns={[
            { key: 'name', label: 'Name', width: '1fr' },
            { key: 'amount', label: 'Balance EUR', width: '140px', align: 'right' },
          ]}
          rows={2}
        />
        <ErrorBlock
          title="Couldn't load subscribers"
          error={new Error('GET /billingAccount → 503 Service Unavailable')}
          onRetry={() => {}}
        />
        <EmptyBlock
          title="No subscribers yet"
          text="A subscriber appears here when a billing account is created for a party."
          action={<Button>New subscriber</Button>}
        />
        <NoResults
          query="nordwnd"
          filters={[
            { label: 'Status', value: 'Suspended' },
            { label: 'Mode', value: 'Prepaid' },
          ]}
          onReset={() => {}}
        />
      </section>
      <section>
        <h2>Buttons</h2>
        {(['primary', 'secondary', 'danger', 'quiet'] as const).map((variant) => (
          <div className="ui-kit-row" key={variant}>
            <Button variant={variant}>{variant}</Button>
            <Button variant={variant} small>
              Small
            </Button>
            <Button variant={variant} disabled>
              Disabled
            </Button>
            <Button variant={variant} busy>
              Saving
            </Button>
            <Button variant={variant} busy busyLabel="Saving…">
              Save
            </Button>
          </div>
        ))}
        <Button variant="quiet" tone="negative">
          Delete…
        </Button>
      </section>
      <section>
        <h2>Fields</h2>
        <div className="ui-kit-fields">
          <Field label="Name" help="As shown on the account">
            <TextInput placeholder="Subscriber name" />
          </Field>
          <Field label="Account" error="Account is required">
            <TextInput aria-invalid="true" />
          </Field>
          <Field label="Disabled">
            <TextInput value="Unavailable" disabled readOnly />
          </Field>
          <Field label="Mode">
            <SelectField defaultValue="prepaid">
              <option value="prepaid">Prepaid</option>
              <option value="postpaid">Postpaid</option>
            </SelectField>
          </Field>
          <Field label="Start date">
            <DateField defaultValue="2026-10-26" />
          </Field>
          <Field label="Amount">
            <MoneyField currency="EUR" value={money} onChange={(e) => setMoney(e.target.value)} />
          </Field>
          <Field label="Confirmed">
            <Checkbox />
          </Field>
        </div>
      </section>
      <section data-ui="ui.uikit.choices">
        <h2>Checkbox, radio and switch</h2>
        <div className="ui-kit-row">
          <Checkbox label="Unchecked" />
          <Checkbox label="Checked" defaultChecked />
          <Checkbox label="Disabled" disabled />
          <Checkbox label="Checked, disabled" defaultChecked disabled />
        </div>
        <div className="ui-kit-row" role="radiogroup" aria-label="Billing cycle">
          <Radio name="kit-cycle" label="Monthly" defaultChecked />
          <Radio name="kit-cycle" label="Quarterly" />
          <Radio name="kit-cycle-off" label="Disabled" disabled />
        </div>
        <div className="ui-kit-row">
          <Switch label="Off" />
          <Switch label="On" defaultChecked />
          <Switch label="Disabled" disabled />
        </div>
      </section>
      <section>
        <h2>Icon buttons and avatars</h2>
        <div className="ui-kit-row">
          <IconButton icon="search" label="Search" />
          <IconButton icon="copy" label="Copy" small />
          <IconButton icon="x" label="Close" disabled />
          {['admin', 'operator', 'olga', 'care', 'billing', 'mk'].map((name) => (
            <Avatar key={name} name={name} initials={name.slice(0, 2).toUpperCase()} />
          ))}
        </div>
      </section>
      <section data-ui="ui.uikit.combobox">
        <h2>Combobox</h2>
        <div className="ui-kit-fields">
          <Field label="Empty">
            <Combobox
              ariaLabel="Empty combobox"
              placeholder="Choose a party"
              options={COMBOBOX_OPTIONS}
              value={owner}
              onChange={setOwner}
            />
          </Field>
          <Field label="Selected">
            <Combobox
              ariaLabel="Selected combobox"
              options={COMBOBOX_OPTIONS}
              value={chosen}
              onChange={setChosen}
            />
          </Field>
          <Field label="Disabled">
            <Combobox
              ariaLabel="Disabled combobox"
              options={COMBOBOX_OPTIONS}
              value="p1"
              onChange={() => {}}
              disabled
            />
          </Field>
        </div>
      </section>
      <section>
        <h2>Status pills</h2>
        {(Object.keys(STATUS_MAP) as StatusKind[]).map((kind) => (
          <div key={kind}>
            <h3>{kind}</h3>
            <div className="ui-kit-row">
              {Object.keys(STATUS_MAP[kind]).map((status) => (
                <StatusPill key={status} kind={kind} status={status} />
              ))}
            </div>
          </div>
        ))}
      </section>
      <section>
        <h2>Tags, flags and badges</h2>
        <div className="ui-kit-row">
          <Tag>Prepaid</Tag>
          <Tag>Postpaid</Tag>
          <Flag>Over limit</Flag>
          <Badge>14</Badge>
          <Badge kind="failed">failed</Badge>
          <Badge kind="info">2</Badge>
        </div>
      </section>
      <section>
        <h2>Amounts and ids</h2>
        <div className="ui-kit-row">
          <Amount cents={-4210} currency="EUR" />
          <Amount cents={1000} signed currency="EUR" />
          <Amount cents={0} />
          <span className="amount-strong">
            <Amount cents={125040} currency="EUR" />
          </span>
          {ID_PREFIXES.map((prefix) => (
            <Id key={prefix} prefix={prefix} id="81d0e7b4-0000-4000-8000-000000000001" />
          ))}
        </div>
      </section>
      <section>
        <h2>Segmented</h2>
        <div className="ui-kit-row">
          <Segmented
            label="Account mode"
            value={mode}
            onChange={setMode}
            options={[
              { key: 'prepaid', label: 'Prepaid', count: 14 },
              { key: 'postpaid', label: 'Postpaid', count: 8 },
            ]}
          />
          <Segmented
            as="radio"
            label="Choose mode"
            value={mode}
            onChange={setMode}
            options={[
              { key: 'prepaid', label: 'Prepaid' },
              { key: 'postpaid', label: 'Postpaid' },
            ]}
          />
        </div>
      </section>
    </div>
  );
}
