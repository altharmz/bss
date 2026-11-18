"""Wholesale reconciliation with the network partner's invoices (#197).

Revision ID: 0083
Revises: 0082

docs/decisions/wholesale.md. An imported partner invoice, its lines (matched to the product
that held the number) and the discrepancies the reconciliation found. Imported rows are
immutable (triggers): a corrected invoice is a new import.
"""

from alembic import op

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE wholesale_invoice (
            id uuid PRIMARY KEY,
            invoice_number text NOT NULL UNIQUE CHECK (invoice_number <> ''),
            period daterange NOT NULL CHECK (NOT isempty(period) AND lower_inc(period)
                AND NOT upper_inc(period) AND NOT lower_inf(period) AND NOT upper_inf(period)),
            currency char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
            total numeric(14,2) NOT NULL,
            line_count integer NOT NULL CHECK (line_count >= 0),
            state text NOT NULL CHECK (state IN ('imported', 'reconciled')),
            imported_at timestamptz NOT NULL,
            imported_by text NOT NULL CHECK (imported_by <> '')
        );
        CREATE INDEX wholesale_invoice_imported_idx ON wholesale_invoice (imported_at DESC, id);
        COMMENT ON TABLE wholesale_invoice IS
            'A wholesale invoice of the network partner (not a TMF678 customer bill), imported by an operator and reconciled against the lines and usage the BSS knows. Immutable once reconciled; a corrected invoice is a new import under another number.';
        COMMENT ON COLUMN wholesale_invoice.period IS 'From the earliest line start to the latest line end (half-open).';
        COMMENT ON COLUMN wholesale_invoice.total IS 'The sum of its line amounts.';
        COMMENT ON COLUMN wholesale_invoice.state IS 'imported (lines stored), reconciled (discrepancies found); the import does both in one transaction.';
        COMMENT ON COLUMN wholesale_invoice.imported_by IS 'The operator who imported it.';

        CREATE TABLE wholesale_invoice_line (
            id uuid PRIMARY KEY,
            invoice_id uuid NOT NULL REFERENCES wholesale_invoice(id),
            line_number integer NOT NULL CHECK (line_number >= 1),
            msisdn text CHECK (msisdn ~ '^[1-9][0-9]{7,14}$'),
            iccid text CHECK (iccid ~ '^[0-9]{18,22}$'),
            period daterange NOT NULL CHECK (NOT isempty(period) AND lower_inc(period)
                AND NOT upper_inc(period) AND NOT lower_inf(period) AND NOT upper_inf(period)),
            charge_type text NOT NULL CHECK (charge_type IN ('line', 'data', 'voice', 'sms', 'other')),
            quantity numeric(18,3) NOT NULL CHECK (quantity >= 0),
            unit text CHECK (unit IN ('MB', 'minute', 'message')),
            amount numeric(14,2) NOT NULL,
            product_id uuid REFERENCES product(id),
            UNIQUE (invoice_id, line_number),
            CHECK (msisdn IS NOT NULL OR iccid IS NOT NULL),
            CHECK ((charge_type IN ('data', 'voice', 'sms')) = (unit IS NOT NULL)),
            CHECK (charge_type <> 'data' OR unit = 'MB'),
            CHECK (charge_type <> 'voice' OR unit = 'minute'),
            CHECK (charge_type <> 'sms' OR unit = 'message')
        );
        CREATE INDEX wholesale_invoice_line_invoice_idx
            ON wholesale_invoice_line (invoice_id, product_id);
        CREATE INDEX wholesale_invoice_line_product_idx ON wholesale_invoice_line (product_id)
            WHERE product_id IS NOT NULL;
        CREATE INDEX wholesale_invoice_line_number_idx ON wholesale_invoice_line (invoice_id, msisdn)
            WHERE charge_type = 'line';
        COMMENT ON TABLE wholesale_invoice_line IS
            'One charge of a wholesale invoice, as imported, with the product it was matched to.';
        COMMENT ON COLUMN wholesale_invoice_line.line_number IS 'Its row in the imported file (1 = the first row after the header).';
        COMMENT ON COLUMN wholesale_invoice_line.period IS 'period_start to period_end of the row, half-open.';
        COMMENT ON COLUMN wholesale_invoice_line.charge_type IS 'line (the monthly access charge), data, voice, sms or other.';
        COMMENT ON COLUMN wholesale_invoice_line.unit IS 'MB for data, minute for voice, message for sms; none for line and other.';
        COMMENT ON COLUMN wholesale_invoice_line.product_id IS 'The product that held the MSISDN on the period start (number assignment history, cancelled claims ignored), else the one whose SIM had the ICCID; NULL when none did.';

        CREATE TABLE wholesale_discrepancy (
            id uuid PRIMARY KEY,
            invoice_id uuid NOT NULL REFERENCES wholesale_invoice(id),
            kind text NOT NULL CHECK (kind IN ('billedNotActive', 'activeNotBilled',
                'usageMismatch', 'duplicateCharge')),
            product_id uuid REFERENCES product(id),
            msisdn text,
            invoice_line_id uuid REFERENCES wholesale_invoice_line(id),
            usage_kind text CHECK (usage_kind IN ('data', 'voice', 'sms')),
            expected numeric(18,3),
            invoiced numeric(18,3),
            detail jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(detail) = 'object'),
            CHECK ((kind = 'usageMismatch') = (usage_kind IS NOT NULL))
        );
        CREATE INDEX wholesale_discrepancy_invoice_idx ON wholesale_discrepancy (invoice_id, kind);
        CREATE INDEX wholesale_discrepancy_product_idx ON wholesale_discrepancy (product_id)
            WHERE product_id IS NOT NULL;
        CREATE INDEX wholesale_discrepancy_line_idx ON wholesale_discrepancy (invoice_line_id)
            WHERE invoice_line_id IS NOT NULL;
        COMMENT ON TABLE wholesale_discrepancy IS
            'What the reconciliation of a wholesale invoice found: billedNotActive (a line charge for a number no product held in its period), activeNotBilled (a prepaid line live in the period without a line charge), usageMismatch (invoiced usage off the BSS total by more than 2 percent and the absolute tolerance), duplicateCharge (two line charges of one number in overlapping periods).';
        COMMENT ON COLUMN wholesale_discrepancy.usage_kind IS 'For a usageMismatch: data, voice or sms.';
        COMMENT ON COLUMN wholesale_discrepancy.expected IS 'What the BSS expects: days live (line kinds), usage in MB, minutes or messages (usageMismatch).';
        COMMENT ON COLUMN wholesale_discrepancy.invoiced IS 'What the invoice says in the same unit.';

        CREATE FUNCTION wholesale_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% rows are immutable: a corrected invoice is a new import',
                TG_TABLE_NAME;
        END $$;
        CREATE TRIGGER wholesale_invoice_line_immutable
            BEFORE UPDATE OR DELETE ON wholesale_invoice_line
            FOR EACH ROW EXECUTE FUNCTION wholesale_immutable();
        CREATE TRIGGER wholesale_discrepancy_immutable
            BEFORE UPDATE OR DELETE ON wholesale_discrepancy
            FOR EACH ROW EXECUTE FUNCTION wholesale_immutable();
        CREATE FUNCTION wholesale_invoice_once() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR OLD.state = 'reconciled' OR NEW.state <> 'reconciled'
               OR (NEW.id, NEW.invoice_number, NEW.period, NEW.currency, NEW.total,
                   NEW.line_count, NEW.imported_at, NEW.imported_by)
                  IS DISTINCT FROM (OLD.id, OLD.invoice_number, OLD.period, OLD.currency,
                   OLD.total, OLD.line_count, OLD.imported_at, OLD.imported_by) THEN
                RAISE EXCEPTION 'wholesale_invoice rows are immutable once reconciled';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER wholesale_invoice_immutable
            BEFORE UPDATE OR DELETE ON wholesale_invoice
            FOR EACH ROW EXECUTE FUNCTION wholesale_invoice_once();
    """)


def downgrade() -> None:
    # a rollback discards imported invoices: re-import them after upgrading again
    op.execute("""
        DROP TABLE wholesale_discrepancy;
        DROP TABLE wholesale_invoice_line;
        DROP TABLE wholesale_invoice;
        DROP FUNCTION wholesale_invoice_once();
        DROP FUNCTION wholesale_immutable();
    """)
