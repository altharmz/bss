export type StatusFamily =
  'live' | 'moving' | 'queued' | 'pending' | 'paused' | 'failed' | 'draft' | 'ended';

export const STATUS_MAP = {
  account: {
    Active: ['live', 'Active'],
    Suspended: ['paused', 'Suspended'],
    Closed: ['ended', 'Closed'],
  },
  customer: {
    Created: ['draft', 'Created'],
    Active: ['live', 'Active'],
    Inactive: ['ended', 'Inactive'],
  },
  party: {
    initialized: ['draft', 'Initialized'],
    validated: ['live', 'Validated'],
    deceased: ['ended', 'Deceased'],
    closed: ['ended', 'Closed'],
  },
  lifecycle: {
    'In study': ['draft', 'In study'],
    'In design': ['draft', 'In design'],
    'In Design': ['draft', 'In design'],
    'In test': ['draft', 'In test'],
    Active: ['live', 'Active'],
    Launched: ['live', 'Launched'],
    Rejected: ['failed', 'Rejected'],
    Retired: ['ended', 'Retired'],
    Obsolete: ['ended', 'Obsolete'],
  },
  product: {
    created: ['draft', 'Created'],
    pendingActive: ['queued', 'Pending active'],
    active: ['live', 'Active'],
    pendingTerminate: ['pending', 'Pending terminate'],
    suspended: ['paused', 'Suspended'],
    terminated: ['ended', 'Terminated'],
    cancelled: ['ended', 'Cancelled'],
  },
  order: {
    draft: ['draft', 'Draft'],
    acknowledged: ['queued', 'Acknowledged'],
    pending: ['queued', 'Pending'],
    inProgress: ['moving', 'In progress'],
    assessingCancellation: ['moving', 'Assessing cancellation'],
    pendingCancellation: ['moving', 'Pending cancellation'],
    held: ['paused', 'Held'],
    completed: ['live', 'Completed'],
    failed: ['failed', 'Failed'],
    partial: ['failed', 'Partial'],
    rejected: ['failed', 'Rejected'],
    cancelled: ['ended', 'Cancelled'],
  },
  run: {
    running: ['moving', 'Running'],
    completed: ['live', 'Completed'],
    failed: ['failed', 'Failed'],
  },
  runOutcome: {
    ok: ['live', 'Billed'],
    blocked: ['pending', 'Waiting'],
    failed: ['failed', 'Failed'],
  },
  command: {
    pending: ['queued', 'Queued'],
    completed: ['live', 'Completed'],
    failedTechnical: ['failed', 'Failed (technical)'],
  },
  bill: {
    new: ['draft', 'New'],
    validated: ['queued', 'Ready to send'],
    sent: ['pending', 'Unpaid'],
    partiallyPaid: ['pending', 'Partly paid'],
    onHold: ['paused', 'On hold'],
    settled: ['ended', 'Settled'],
  },
  onDemandBill: {
    inProgress: ['moving', 'In progress'],
    done: ['live', 'Done'],
    rejected: ['failed', 'Rejected'],
    terminatedWithError: ['failed', 'Terminated with error'],
  },
  payment: {
    pendingAuthorization: ['queued', 'Pending authorization'],
    authorized: ['live', 'Authorized'],
    captured: ['live', 'Captured'],
    done: ['live', 'Done'],
    failed: ['failed', 'Failed'],
    denied: ['failed', 'Denied'],
    canceled: ['ended', 'Cancelled'],
  },
  bucket: {
    active: ['live', 'Active'],
    suspended: ['paused', 'Suspended'],
    expired: ['ended', 'Expired'],
  },
  balanceAction: {
    created: ['queued', 'Accepted'],
    completed: ['live', 'Completed'],
    failed: ['failed', 'Failed'],
    cancelled: ['ended', 'Cancelled'],
  },
  delivery: {
    pending: ['queued', 'Pending'],
    delivered: ['live', 'Delivered'],
    failed: ['failed', 'Failed'],
    dropped: ['ended', 'Dropped'],
  },
  catalogJob: {
    'Not Started': ['queued', 'Not started'],
    Running: ['moving', 'Running'],
    Succeeded: ['live', 'Succeeded'],
    Failed: ['failed', 'Failed'],
  },
  health: {
    ok: ['live', 'OK'],
    error: ['failed', 'Error'],
  },
  usage: {
    notRated: ['queued', 'Not rated'],
    rated: ['live', 'Rated'],
    rejected: ['failed', 'Rejected'],
  },
} as const;

export type StatusKind = keyof typeof STATUS_MAP;

export function statusPresentation(
  kind: StatusKind,
  status: string,
): readonly [StatusFamily, string] {
  const values: Record<string, readonly [StatusFamily, string]> = STATUS_MAP[kind];
  const presentation = Object.hasOwn(values, status) ? values[status] : undefined;
  return presentation ?? ['draft', status];
}
