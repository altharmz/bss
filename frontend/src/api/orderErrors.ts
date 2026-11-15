/** Account security refusals (docs/decisions/accounts.md): a rejected order's code, or a 403's
 * `code` on the internal and party/account writes. `explain` (api/client) uses them too. */
export const ACCOUNT_SECURITY_ERRORS: Record<string, string> = {
  pinRequired: 'Ask the customer for their account PIN and enter it in the Account PIN field.',
  pinInvalid: 'The account PIN is wrong. Repeated wrong PINs lock the verification.',
  pinNotSet:
    'The customer has no account PIN yet. Set one on the subscriber card’s Security tab first.',
  pinLocked:
    'Too many wrong account PINs: verification is locked for a while. Try later or reset the PIN.',
  simSwapLocked:
    'This line has a SIM swap lock. Clear it on the subscriber card’s Security tab first.',
  portOutLocked:
    'This line has a port-out lock. Clear it on the subscriber card’s Security tab first.',
};

/** Operator explanations for every TMF622 business refusal. Unknown codes retain the server reason. */
export const ORDER_ERRORS: Record<string, string> = {
  ...ACCOUNT_SECURITY_ERRORS,
  accountNotActive: 'The billing account must be active for this order.',
  addOnParentNotActive: 'The plan has no paid period running today. Renew it first.',
  addOnParentNotPlan: 'Choose a plan product for the add-on, not another add-on.',
  addOnParentRequired: 'Choose the plan product this add-on is for.',
  addOnParentUnknown: "The plan product is not one of this billing account's products.",
  billingAccountMismatch: 'Choose a product from the same billing account as the order.',
  combinedChangeUnavailable: 'Change the offering and subscriber number in separate orders.',
  deviceChangeAlone: 'Change the device in an order item of its own, changing nothing else.',
  deviceIncompatible: 'The device does not work on the network. Use another device.',
  deviceNotApplicable: 'A device is recorded on prepaid network lines only.',
  imsiNotRequired: 'This product does not require an IMSI.',
  imsiRequired: 'Choose exactly one IMSI.',
  imsiUnavailable: 'This IMSI is unavailable (unknown, locked or in use). Choose another.',
  insufficientFunds: 'Top up the account before placing this order.',
  invalidImei: 'The IMEI must be 15 digits with a correct check digit.',
  mixedOfferingKinds: 'Order plans, add-on packs and pay-as-you-go lines separately.',
  noOneTimePrice: 'This add-on has no one-time price in the account currency on this date.',
  noRecurringPrice: 'No recurring price is available in the account currency on this date.',
  numberNotRequired: 'This product does not require a subscriber number.',
  numberRequired: 'Choose exactly one subscriber number.',
  numberRequirementDiffers:
    'Choose an offering with the same subscriber number requirement as this product.',
  numberUnavailable: 'This subscriber number is unavailable. Choose another number.',
  offeringChangeNotAtRenewal:
    'Choose a date from today to the prepaid product’s paid-through date for the offering change.',
  offeringChangePending:
    'Another order already changes this prepaid offering. Undo that change first.',
  offeringNotSellable: 'This offering is no longer available for sale.',
  offeringNotValid: 'This offering is not valid on the selected date.',
  offeringUnchanged: 'The product already uses this offering on the selected date.',
  openDeleteItem: 'Another order already ends this product. Undo that order first.',
  openModifyItem: 'Another order already changes this product. Undo that change first.',
  prepaidLifecycleUnavailable: 'This combination of changes is unavailable for prepaid products.',
  prepaidOnly: 'This offering is for prepaid accounts only.',
  priceEnds: 'The recurring price ends without a successor. Update the catalog first.',
  productAlreadyEnded:
    'The product has ended or has an end scheduled. Undo its end first if available.',
  productNotStarted:
    'The product has not started. Choose its start date or later, or cancel its purchase.',
  productOfOtherAccount: 'This product belongs to another billing account.',
  realizingResourceDuplicate: 'Choose at most one resource of each kind.',
  requestedStartDateInPast: 'Choose today or a later start date.',
  serviceAddressRequired:
    'The billing account needs a service address before a prepaid line can be activated.',
  simSwapAlone:
    'Swap the SIM of a prepaid line in an order of its own: change only the IMSI, nothing else.',
  simUnavailable: 'No SIM of the chosen type is in stock. Import SIMs or choose another type.',
  startNotToday: 'Add-ons and pay-as-you-go lines start today.',
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
