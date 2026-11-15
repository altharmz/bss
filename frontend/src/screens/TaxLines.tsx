import { formatAmount } from '../format/money';

// The tax lines of a charge (#207, docs/decisions/taxes.md): TMF678 appliedTax, one row per
// line. The tax engine's lines carry a jurisdiction; a fixed fee has rate 0, shown as "—".

export interface TaxLine {
  taxCategory?: string;
  taxRate?: number;
  taxAmount?: { value?: number; unit?: string };
  jurisdiction?: string;
}

const CATEGORY: Record<string, string> = {
  sales_tax: 'Sales tax',
  federal_usf: 'Federal USF',
  prepaid_wireless_e911: 'Prepaid wireless E911',
};

export const taxCategoryLabel = (code: string) => CATEGORY[code] ?? code;

/** The appliedTax of a loaded body, when it is a list (a raw GET is not typed). */
export function taxLinesOf(value: unknown): TaxLine[] {
  return Array.isArray(value) ? (value as TaxLine[]) : [];
}

export function TaxLines({ lines }: { lines: readonly TaxLine[] }) {
  if (lines.length === 0) return null;
  return (
    <table className="bss-secondary" aria-label="Tax lines" data-ui="ui.bills.rateDetail.taxLines">
      <thead>
        <tr>
          <th>Tax</th>
          <th>Jurisdiction</th>
          <th>Rate</th>
          <th>Amount</th>
        </tr>
      </thead>
      <tbody>
        {lines.map((line, i) => (
          <tr key={i}>
            <td title={line.taxCategory}>{taxCategoryLabel(line.taxCategory ?? '—')}</td>
            <td>{line.jurisdiction ?? '—'}</td>
            <td className="num">{line.taxRate ? `${line.taxRate}%` : '—'}</td>
            <td className="num">
              {line.taxAmount?.value === undefined ? '—' : formatAmount(line.taxAmount.value)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
