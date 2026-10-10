// Validation of the New offering form (README "Modal 6"). The design's messages are kept
// verbatim; the rules the design does not have are the backend's own, checked here so
// the operator learns before submitting:
// - the code pattern (migration 0003: ^[A-Z0-9][A-Z0-9_-]{0,31}$, upper case);
// - a price never starts in the past, and an order prices products at midnight, so the
//   earliest useful start is tomorrow (docs/decisions/catalog.md, TMF620 mapping).
// The backend still decides: a 409/422 from it is shown as is.

import { addDays, isIsoDate } from '../format/dates';
import { parseAmount } from '../format/money';

export interface OfferingForm {
  code: string;
  name: string;
  specificationId: string;
  price: string;
  validFrom: string;
  validTo: string;
}

export interface Checked {
  error: string | null; // the one message shown (12px red), the first that applies
  ready: boolean; // the Create button is enabled
  code: string; // upper-cased
  amount: string | null; // canonical decimal string when the price is valid
}

const CODE = /^[A-Z0-9][A-Z0-9_-]{0,31}$/;

export function checkOffering(form: OfferingForm, takenCodes: Set<string>, today: string): Checked {
  const code = form.code.trim().toUpperCase();
  const parsed = form.price.trim() ? parseAmount(form.price) : null;
  const amount = parsed !== null && parsed !== '0.00' ? parsed : null;
  const earliest = addDays(today, 1);

  let error: string | null = null;
  if (code && takenCodes.has(code)) error = `Code ${code} is already in use`;
  else if (code && !CODE.test(code))
    error = 'Code may use A-Z, 0-9, "-" and "_" (up to 32 characters)';
  else if (form.price.trim() && amount === null) error = 'Price must be a positive number';
  else if (form.validFrom && (!isIsoDate(form.validFrom) || form.validFrom < earliest)) {
    error = `Valid from cannot be earlier than ${earliest}`;
  } else if (form.validTo && form.validFrom && form.validTo < form.validFrom) {
    error = 'End date is before start date';
  }

  const ready =
    error === null &&
    code !== '' &&
    form.name.trim() !== '' &&
    form.specificationId !== '' &&
    amount !== null &&
    form.validFrom !== '';
  return { error, ready, code, amount };
}
