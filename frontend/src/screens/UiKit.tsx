import { TableHead, TableRow } from '../ui/Table';
import { EmptyBlock, ErrorBlock, NoResults, SkeletonRows } from '../ui/states';
import { Amount } from '../ui/Amount';
import { Id, ID_PREFIXES } from '../ui/Id';
import { STATUS_MAP, type StatusKind } from '../ui/status';
import { useState } from 'react';
import {
  Badge,
  Flag,
  StatusPill,
  Tag,
  Button,
  Checkbox,
  DateField,
  Field,
  MoneyField,
  Segmented,
  SelectField,
  TextInput,
} from '../ui/kit';
import './screens.css';

/** Ledger Components artboard: live controls without screen-specific requests. */
export function UiKitScreen() {
  const [mode, setMode] = useState('prepaid');
  const [money, setMoney] = useState('42.10');
  return (
    <div className="ui-kit" data-ui="ui.kit">
      <h1>UI kit</h1>
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
