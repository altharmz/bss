/** Operator explanations for every TMF622 business refusal. Unknown codes retain the server reason. */
export const ORDER_ERRORS: Record<string, string> = {
  accountNotActive: 'The billing account must be active for this order.',
  billingAccountMismatch: 'Choose a product from the same billing account as the order.',
  combinedChangeUnavailable: 'Change the offering and subscriber number in separate orders.',
  imsiNotRequired: 'This product does not require an IMSI.',
  imsiRequired: 'Choose exactly one IMSI.',
  imsiUnavailable: 'This IMSI is unavailable (unknown, locked or in use). Choose another.',
  insufficientFunds: 'Top up the account before placing this order.',
  noRecurringPrice: 'No recurring price is available in the account currency on this date.',
  numberNotRequired: 'This product does not require a subscriber number.',
  numberRequired: 'Choose exactly one subscriber number.',
  numberRequirementDiffers:
    'Choose an offering with the same subscriber number requirement as this product.',
  numberUnavailable: 'This subscriber number is unavailable. Choose another number.',
  offeringChangeNotAtRenewal: 'Choose the prepaid product’s renewal date for the offering change.',
  offeringNotSellable: 'This offering is no longer available for sale.',
  offeringNotValid: 'This offering is not valid on the selected date.',
  offeringUnchanged: 'The product already uses this offering on the selected date.',
  openDeleteItem: 'Another order already ends this product. Undo that order first.',
  openModifyItem: 'Another order already changes this product. Undo that change first.',
  prepaidLifecycleUnavailable: 'This combination of changes is unavailable for prepaid products.',
  priceEnds: 'The recurring price ends without a successor. Update the catalog first.',
  productAlreadyEnded:
    'The product has ended or has an end scheduled. Undo its end first if available.',
  productNotStarted:
    'The product has not started. Choose its start date or later, or cancel its purchase.',
  productOfOtherAccount: 'This product belongs to another billing account.',
  realizingResourceDuplicate: 'Choose at most one resource of each kind.',
  requestedStartDateInPast: 'Choose today or a later start date.',
  statusUnchanged: 'The product already has the requested status.',
  terminationInPeriod:
    'The product ends in this billing period. Undo its end or choose an earlier period.',
};

export type OrderError = { code?: string; reason?: string };

export function orderRefusal(errors: readonly OrderError[], state = 'rejected') {
  return {
    problem:
      errors
        .map(
          ({ code, reason }) => (code && ORDER_ERRORS[code]) || reason || 'The order was refused.',
        )
        .join(' ') || `The order was ${state}.`,
    code:
      errors
        .map(({ code }) => code)
        .filter(Boolean)
        .join(', ') || undefined,
  };
}
