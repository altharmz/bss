import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Badge, Flag, StatusPill, Tag } from './kit';
import type { StatusKind } from './status';
import { UiKitScreen } from '../screens/UiKit';

const cases: [StatusKind, string, string, string][] = [
  ['account', 'Active', 'live', 'Active'],
  ['account', 'Suspended', 'paused', 'Suspended'],
  ['account', 'Closed', 'ended', 'Closed'],
  ['customer', 'Created', 'draft', 'Created'],
  ['customer', 'Active', 'live', 'Active'],
  ['customer', 'Inactive', 'ended', 'Inactive'],
  ['party', 'initialized', 'draft', 'Initialized'],
  ['party', 'validated', 'live', 'Validated'],
  ['party', 'deceased', 'ended', 'Deceased'],
  ['party', 'closed', 'ended', 'Closed'],
  ['lifecycle', 'In study', 'draft', 'In study'],
  ['lifecycle', 'In design', 'draft', 'In design'],
  ['lifecycle', 'In Design', 'draft', 'In design'],
  ['lifecycle', 'In test', 'draft', 'In test'],
  ['lifecycle', 'Active', 'live', 'Active'],
  ['lifecycle', 'Launched', 'live', 'Launched'],
  ['lifecycle', 'Rejected', 'failed', 'Rejected'],
  ['lifecycle', 'Retired', 'ended', 'Retired'],
  ['lifecycle', 'Obsolete', 'ended', 'Obsolete'],
  ['product', 'created', 'draft', 'Created'],
  ['product', 'pendingActive', 'queued', 'Pending active'],
  ['product', 'active', 'live', 'Active'],
  ['product', 'pendingTerminate', 'pending', 'Pending terminate'],
  ['product', 'suspended', 'paused', 'Suspended'],
  ['product', 'terminated', 'ended', 'Terminated'],
  ['product', 'cancelled', 'ended', 'Cancelled'],
  ['order', 'draft', 'draft', 'Draft'],
  ['order', 'acknowledged', 'queued', 'Acknowledged'],
  ['order', 'pending', 'queued', 'Pending'],
  ['order', 'inProgress', 'moving', 'In progress'],
  ['order', 'assessingCancellation', 'moving', 'Assessing cancellation'],
  ['order', 'pendingCancellation', 'moving', 'Pending cancellation'],
  ['order', 'held', 'paused', 'Held'],
  ['order', 'completed', 'live', 'Completed'],
  ['order', 'failed', 'failed', 'Failed'],
  ['order', 'partial', 'failed', 'Partial'],
  ['order', 'rejected', 'failed', 'Rejected'],
  ['order', 'cancelled', 'ended', 'Cancelled'],
  ['run', 'running', 'moving', 'Running'],
  ['run', 'completed', 'live', 'Completed'],
  ['run', 'failed', 'failed', 'Failed'],
  ['runOutcome', 'ok', 'live', 'Billed'],
  ['runOutcome', 'blocked', 'pending', 'Waiting'],
  ['runOutcome', 'failed', 'failed', 'Failed'],
  ['command', 'pending', 'queued', 'Queued'],
  ['command', 'completed', 'live', 'Completed'],
  ['command', 'failedTechnical', 'failed', 'Failed (technical)'],
  ['network', 'pending', 'queued', 'Pending'],
  ['network', 'confirmed', 'live', 'Confirmed'],
  ['network', 'refused', 'failed', 'Refused'],
  ['network', 'exhausted', 'failed', 'Exhausted'],
  ['network', 'cancelled', 'ended', 'Cancelled'],
  ['bill', 'new', 'draft', 'New'],
  ['bill', 'validated', 'queued', 'Ready to send'],
  ['bill', 'sent', 'pending', 'Unpaid'],
  ['bill', 'partiallyPaid', 'pending', 'Partly paid'],
  ['bill', 'onHold', 'paused', 'On hold'],
  ['bill', 'settled', 'ended', 'Settled'],
  ['onDemandBill', 'inProgress', 'moving', 'In progress'],
  ['onDemandBill', 'done', 'live', 'Done'],
  ['onDemandBill', 'rejected', 'failed', 'Rejected'],
  ['onDemandBill', 'terminatedWithError', 'failed', 'Terminated with error'],
  ['payment', 'pendingAuthorization', 'queued', 'Pending authorization'],
  ['payment', 'authorized', 'live', 'Authorized'],
  ['payment', 'captured', 'live', 'Captured'],
  ['payment', 'done', 'live', 'Done'],
  ['payment', 'failed', 'failed', 'Failed'],
  ['payment', 'denied', 'failed', 'Denied'],
  ['payment', 'canceled', 'ended', 'Cancelled'],
  ['bucket', 'active', 'live', 'Active'],
  ['bucket', 'suspended', 'paused', 'Suspended'],
  ['bucket', 'expired', 'ended', 'Expired'],
  ['balanceAction', 'created', 'queued', 'Accepted'],
  ['balanceAction', 'completed', 'live', 'Completed'],
  ['balanceAction', 'failed', 'failed', 'Failed'],
  ['balanceAction', 'cancelled', 'ended', 'Cancelled'],
  ['delivery', 'pending', 'queued', 'Pending'],
  ['delivery', 'delivered', 'live', 'Delivered'],
  ['delivery', 'failed', 'failed', 'Failed'],
  ['delivery', 'dropped', 'ended', 'Dropped'],
  ['catalogJob', 'Not Started', 'queued', 'Not started'],
  ['catalogJob', 'Running', 'moving', 'Running'],
  ['catalogJob', 'Succeeded', 'live', 'Succeeded'],
  ['catalogJob', 'Failed', 'failed', 'Failed'],
  ['health', 'ok', 'live', 'OK'],
  ['health', 'error', 'failed', 'Error'],
  ['usage', 'notRated', 'queued', 'Not rated'],
  ['usage', 'rated', 'live', 'Rated'],
  ['usage', 'rejected', 'failed', 'Rejected'],
  ['message', 'initial', 'queued', 'Queued'],
  ['message', 'inProgress', 'moving', 'Retrying'],
  ['message', 'completed', 'live', 'Sent'],
  ['message', 'cancelled', 'ended', 'Cancelled'],
  ['message', 'failed', 'failed', 'Failed'],
];

describe('D4 status families', () => {
  it('previews every kind and value in the gallery', () => {
    const { container } = render(<UiKitScreen />);
    expect(container.querySelectorAll('.status-pill').length).toBe(cases.length);
    const pills = Array.from(container.querySelectorAll('.status-pill'));
    cases.forEach(([, , family, label], index) => {
      expect(pills[index]?.textContent).toBe(label);
      expect(pills[index]?.classList.contains(`pill-${family}`)).toBe(true);
    });
  });
  it.each(cases)('%s %s renders %s and %s', (kind, status, family, label) => {
    render(<StatusPill kind={kind} status={status} />);
    expect(screen.getByText(label).classList.contains(`pill-${family}`)).toBe(true);
  });
  it.each(['futureState', '__proto__', 'toString'])(
    'shows unknown %s in the Draft family',
    (status) => {
      render(<StatusPill kind="account" status={status} />);
      expect(screen.getByText(status).classList.contains('pill-draft')).toBe(true);
    },
  );
  it('preserves a custom display label', () => {
    render(<StatusPill kind="product" status="pendingTerminate" label="ends tomorrow" />);
    expect(screen.getByText('ends tomorrow').classList.contains('pill-pending')).toBe(true);
  });
  it('keeps attributes, warnings and badge kinds distinct', () => {
    render(
      <>
        <Tag>Prepaid</Tag>
        <Flag>Over limit</Flag>
        <Badge>14</Badge>
        <Badge kind="failed">failed</Badge>
        <Badge kind="info">2</Badge>
      </>,
    );
    expect(screen.getByText('Prepaid').classList.contains('bss-tag')).toBe(true);
    expect(screen.getByText('Over limit').classList.contains('bss-flag')).toBe(true);
    expect(screen.getByText('14').classList.contains('badge-count')).toBe(true);
    expect(screen.getByText('failed').classList.contains('badge-failed')).toBe(true);
    expect(screen.getByText('2').classList.contains('badge-info')).toBe(true);
  });
});
