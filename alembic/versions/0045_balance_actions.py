"""TMF654 balance actions, reversals and adjustment/transfer/reversal funding sources.

Schema and combined bounds only; the action APIs and the transfer worker follow.
"""

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = depends_on = None

# Tables whose writes can change a source's usage or an action's ledger effect.
BOUNDED = (
    "balance_action", "balance_action_reversal", "funding_source", "funding_consumption",
    "payment", "customer_bill", "bill_allocation", "refund_source",
    "funding_hold", "funding_hold_source", "funding_conversion_source",
)  # fmt: skip
SOURCE_COLUMN = {
    "bill_allocation": "bill_allocation_one_source",
    "refund_source": "refund_source_check",
    "funding_hold_source": "funding_hold_source_check",
}

BOUNDS = r"""
CREATE FUNCTION balance_ledger_check(acct uuid) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF acct IS NULL OR NOT EXISTS (SELECT FROM prepaid_bucket WHERE billing_account_id = acct) THEN
        RETURN;  -- every action, source and consumption belongs to a prepaid bucket
    END IF;
    -- Combined usage of every source kind: allocations, payouts, outstanding holds, debits.
    IF EXISTS (
        SELECT FROM payment p WHERE p.billing_account_id = acct
        AND coalesce((SELECT sum(amount) FROM bill_allocation WHERE payment_id = p.id), 0)
          + coalesce((SELECT sum(amount) FROM refund_source WHERE payment_id = p.id), 0)
          + coalesce((SELECT sum(s.amount) FROM funding_hold_source s JOIN funding_hold h ON h.id = s.hold_id
                      WHERE s.payment_id = p.id AND h.released_at IS NULL), 0)
          + coalesce((SELECT sum(amount) FROM funding_consumption WHERE payment_id = p.id), 0) > p.amount
    ) OR EXISTS (
        SELECT FROM customer_bill b WHERE b.billing_account_id = acct
        AND EXISTS (SELECT FROM funding_consumption WHERE credit_bill_id = b.id)
        AND (b.amount_due >= 0 OR b.state <> 'settled' OR
             coalesce((SELECT sum(amount) FROM bill_allocation WHERE credit_bill_id = b.id), 0)
           + coalesce((SELECT sum(amount) FROM refund_source WHERE credit_bill_id = b.id), 0)
           + coalesce((SELECT sum(s.amount) FROM funding_hold_source s JOIN funding_hold h ON h.id = s.hold_id
                       WHERE s.credit_bill_id = b.id AND h.released_at IS NULL), 0)
           + coalesce((SELECT sum(amount) FROM funding_consumption WHERE credit_bill_id = b.id), 0)
             > -b.amount_due)
    ) OR EXISTS (
        SELECT FROM funding_source f WHERE f.billing_account_id = acct
        AND coalesce((SELECT sum(amount) FROM bill_allocation WHERE funding_source_id = f.id), 0)
          + coalesce((SELECT sum(amount) FROM refund_source WHERE funding_source_id = f.id), 0)
          + coalesce((SELECT sum(s.amount) FROM funding_hold_source s JOIN funding_hold h ON h.id = s.hold_id
                      WHERE s.funding_source_id = f.id AND h.released_at IS NULL), 0)
          + coalesce((SELECT sum(amount) FROM funding_consumption WHERE funding_source_id = f.id), 0) > f.amount
    ) OR EXISTS (
        SELECT FROM funding_conversion_source m
        JOIN funding_hold_source s ON s.id = m.hold_source_id
        JOIN bill_allocation a ON a.id = m.allocation_id
        WHERE m.billing_account_id = acct AND a.funding_source_id IS DISTINCT FROM s.funding_source_id
    ) THEN
        RAISE EXCEPTION 'funding sources exceed their combined bounds' USING ERRCODE = 'check_violation';
    END IF;

    -- Each source is introduced by exactly the action effect it records.
    IF EXISTS (
        SELECT FROM funding_source f JOIN balance_action a ON a.id = f.action_id
        WHERE f.billing_account_id = acct AND (CASE f.kind
            WHEN 'adjustment' THEN a.kind = 'adjustment' AND a.amount > 0
                AND a.status IN ('completed', 'cancelled') AND f.amount = a.amount
                AND f.billing_account_id = a.billing_account_id AND f.consumption_id IS NULL
            WHEN 'transfer' THEN a.kind = 'transfer' AND a.status = 'completed'
                AND f.amount = a.amount AND f.billing_account_id = a.receiver_billing_account_id
                AND f.consumption_id IS NULL
            ELSE a.kind = 'adjustment' AND a.amount < 0 AND a.status = 'cancelled'
                AND f.billing_account_id = a.billing_account_id
                AND EXISTS (SELECT FROM funding_consumption c WHERE c.id = f.consumption_id
                            AND c.action_id = a.id AND c.reversal_action_id IS NULL
                            AND c.amount = f.amount)
        END) IS NOT TRUE
    ) THEN
        RAISE EXCEPTION 'funding source does not match its balance action' USING ERRCODE = 'check_violation';
    END IF;

    -- Ledger effects of each action match its retained sources, consumption and reversal.
    IF EXISTS (
        SELECT FROM balance_action a
        LEFT JOIN balance_action_reversal r ON r.action_id = a.id
        LEFT JOIN funding_hold h ON h.id = a.hold_id
        LEFT JOIN prepaid_command k ON k.id = a.command_id
        WHERE (a.billing_account_id = acct OR a.receiver_billing_account_id = acct) AND (
            -- debits and outgoing principal consume exactly their amount
            coalesce((SELECT sum(amount) FROM funding_consumption
                      WHERE action_id = a.id AND reversal_action_id IS NULL), 0)
              <> CASE WHEN a.kind = 'adjustment' AND a.amount < 0
                       AND a.status IN ('completed', 'cancelled') THEN -a.amount
                      WHEN a.kind = 'transfer' AND a.status = 'completed' THEN a.amount
                      ELSE 0 END
            -- credits and received principal introduce exactly one source
            OR (SELECT count(*) FROM funding_source
                WHERE action_id = a.id AND kind IN ('adjustment', 'transfer'))
              <> CASE WHEN a.kind = 'adjustment' AND a.amount > 0
                       AND a.status IN ('completed', 'cancelled') THEN 1
                      WHEN a.kind = 'transfer' AND a.status = 'completed' THEN 1
                      ELSE 0 END
            -- at most one reversal (primary key), exactly for cancelled top-ups/adjustments
            OR (r.action_id IS NOT NULL)
              <> (a.status = 'cancelled' AND a.kind IN ('topup', 'adjustment'))
            -- a reversal consumes the credit it compensates, or restores a debit's sources
            OR coalesce((SELECT sum(amount) FROM funding_consumption WHERE reversal_action_id = a.id), 0)
              <> CASE WHEN r.action_id IS NOT NULL AND a.amount > 0 THEN a.amount ELSE 0 END
            OR EXISTS (
                SELECT FROM funding_consumption c WHERE c.reversal_action_id = a.id AND (
                    (a.kind = 'topup' AND c.payment_id = a.payment_id)
                    OR (a.kind = 'adjustment' AND c.funding_source_id IN (
                        SELECT id FROM funding_source WHERE action_id = a.id AND kind = 'adjustment'))
                ) IS NOT TRUE)
            OR (SELECT count(*) FROM funding_source WHERE action_id = a.id AND kind = 'reversal')
              <> CASE WHEN r.action_id IS NOT NULL AND a.amount < 0 THEN
                     (SELECT count(*) FROM funding_consumption
                      WHERE action_id = a.id AND reversal_action_id IS NULL)
                 ELSE 0 END
            -- a top-up records exactly one done payment
            OR (a.kind = 'topup' AND a.payment_id IS NOT NULL AND NOT EXISTS (
                SELECT FROM payment p WHERE p.id = a.payment_id
                AND p.amount = a.amount AND p.status = 'done'))
            -- holds follow the action lifecycle
            OR (h.id IS NOT NULL AND (
                h.amount <> a.amount + CASE WHEN a.kind = 'transfer' AND a.cost_owner = 'originator'
                                            THEN a.transfer_cost ELSE 0 END
                OR (a.status = 'created') <> (h.released_at IS NULL)
                OR (a.kind = 'reservation' AND (a.status = 'completed') <> EXISTS (
                    SELECT FROM funding_hold_conversion WHERE hold_id = h.id))
                OR (a.command_id IS NULL AND EXISTS (
                    SELECT FROM funding_command_hold WHERE hold_id = h.id))))
            OR (k.id IS NOT NULL AND (k.billing_account_id <> a.billing_account_id OR NOT CASE a.kind
                WHEN 'transfer' THEN k.kind = 'transfer' ELSE k.kind IN ('order', 'renewal') END))
            OR coalesce(r.amount <> -a.amount, false)
        )
    ) THEN
        RAISE EXCEPTION 'balance action does not match its ledger entries' USING ERRCODE = 'check_violation';
    END IF;
END $$;

CREATE FUNCTION balance_ledger_bounds() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM balance_ledger_check(NEW.billing_account_id);
    IF TG_TABLE_NAME = 'balance_action' THEN
        PERFORM balance_ledger_check(NEW.receiver_billing_account_id);
    END IF;
    RETURN NULL;
END $$;
"""


def upgrade():
    op.execute("""
        ALTER TABLE funding_command_hold ADD CONSTRAINT funding_command_hold_identity
            UNIQUE (command_id, hold_id);
        CREATE TABLE balance_action (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            kind text NOT NULL CHECK (kind IN ('topup', 'reservation', 'adjustment', 'transfer')),
            status text NOT NULL CHECK (status IN ('created', 'failed', 'cancelled', 'completed')),
            billing_account_id uuid NOT NULL,
            currency text NOT NULL,
            bucket_id uuid NOT NULL,
            request jsonb NOT NULL CHECK (jsonb_typeof(request) = 'object'),
            amount numeric(12,2) NOT NULL CHECK (amount <> 'NaN'::numeric),
            transfer_cost numeric(12,2) CHECK (transfer_cost >= 0 AND transfer_cost <> 'NaN'::numeric),
            cost_owner text CHECK (cost_owner IN ('originator', 'receiver')),
            receiver_bucket_id uuid,
            receiver_billing_account_id uuid,
            receiver_currency text,
            reason text,
            description text,
            failure_reason text CHECK (failure_reason <> ''),
            operator text NOT NULL CHECK (operator <> ''),
            requested_at timestamptz NOT NULL CHECK (isfinite(requested_at)),
            confirmed_at timestamptz CHECK (confirmed_at >= requested_at),
            cancelled_at timestamptz CHECK (cancelled_at >= requested_at),
            payment_id uuid UNIQUE,
            hold_id uuid UNIQUE,
            command_id uuid UNIQUE REFERENCES prepaid_command(id),
            UNIQUE (id, billing_account_id),
            UNIQUE (id, billing_account_id, currency),
            FOREIGN KEY (bucket_id, billing_account_id, currency)
                REFERENCES prepaid_bucket(id, billing_account_id, currency),
            FOREIGN KEY (receiver_bucket_id, receiver_billing_account_id, receiver_currency)
                REFERENCES prepaid_bucket(id, billing_account_id, currency),
            FOREIGN KEY (payment_id, billing_account_id) REFERENCES payment(id, billing_account_id),
            FOREIGN KEY (hold_id, billing_account_id) REFERENCES funding_hold(id, billing_account_id),
            FOREIGN KEY (command_id, hold_id) REFERENCES funding_command_hold(command_id, hold_id),
            CONSTRAINT balance_action_status_times CHECK (
                (status = 'failed') = (failure_reason IS NOT NULL)
                AND (status = 'cancelled') = (cancelled_at IS NOT NULL)
                AND (status NOT IN ('completed', 'failed') OR confirmed_at IS NOT NULL)
                AND (status <> 'created' OR confirmed_at IS NULL)),
            CONSTRAINT balance_action_receiver CHECK (
                num_nonnulls(receiver_bucket_id, receiver_billing_account_id, receiver_currency) IN (0, 3)),
            CONSTRAINT balance_action_kind_fields CHECK (CASE kind
                WHEN 'topup' THEN amount > 0 AND status <> 'created'
                    AND (status = 'failed') = (payment_id IS NULL)
                    AND num_nonnulls(transfer_cost, cost_owner, receiver_bucket_id, hold_id, command_id) = 0
                WHEN 'adjustment' THEN amount <> 0 AND status <> 'created'
                    AND num_nonnulls(transfer_cost, cost_owner, receiver_bucket_id,
                                     payment_id, hold_id, command_id) = 0
                WHEN 'reservation' THEN amount >= 0 AND (status = 'failed' OR hold_id IS NOT NULL)
                    AND num_nonnulls(transfer_cost, cost_owner, receiver_bucket_id, payment_id) = 0
                ELSE amount > 0 AND status <> 'cancelled' AND payment_id IS NULL
                    AND transfer_cost IS NOT NULL AND cost_owner IS NOT NULL
                    AND (cost_owner = 'originator' OR transfer_cost <= amount)
                    AND (status = 'failed' OR (
                        num_nonnulls(hold_id, command_id, receiver_bucket_id) = 3
                        AND receiver_currency = currency AND receiver_bucket_id <> bucket_id))
                END)
        );
        CREATE INDEX ON balance_action(billing_account_id);
        CREATE INDEX ON balance_action(receiver_billing_account_id);
        CREATE INDEX ON balance_action(bucket_id);

        CREATE TABLE balance_action_reversal (
            action_id uuid PRIMARY KEY,
            billing_account_id uuid NOT NULL,
            currency text NOT NULL,
            amount numeric(12,2) NOT NULL CHECK (amount <> 0 AND amount <> 'NaN'::numeric),
            operator text NOT NULL CHECK (operator <> ''),
            reversed_at timestamptz NOT NULL CHECK (isfinite(reversed_at)),
            UNIQUE (action_id, billing_account_id),
            FOREIGN KEY (action_id, billing_account_id, currency)
                REFERENCES balance_action(id, billing_account_id, currency)
        );
        CREATE INDEX ON balance_action_reversal(billing_account_id);

        CREATE TABLE funding_consumption (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            billing_account_id uuid NOT NULL,
            action_id uuid NOT NULL,
            reversal_action_id uuid CHECK (reversal_action_id = action_id),
            payment_id uuid,
            credit_bill_id uuid,
            funding_source_id uuid,
            amount numeric(12,2) NOT NULL CHECK (amount > 0 AND amount <> 'NaN'::numeric),
            consumed_at timestamptz NOT NULL CHECK (isfinite(consumed_at)),
            UNIQUE (id, billing_account_id),
            FOREIGN KEY (action_id, billing_account_id) REFERENCES balance_action(id, billing_account_id),
            FOREIGN KEY (reversal_action_id, billing_account_id)
                REFERENCES balance_action_reversal(action_id, billing_account_id),
            FOREIGN KEY (payment_id, billing_account_id) REFERENCES payment(id, billing_account_id),
            FOREIGN KEY (credit_bill_id, billing_account_id) REFERENCES customer_bill(id, billing_account_id),
            CHECK (num_nonnulls(payment_id, credit_bill_id, funding_source_id) = 1)
        );
        CREATE UNIQUE INDEX funding_consumption_once ON funding_consumption
            (action_id, (reversal_action_id IS NOT NULL), coalesce(payment_id, credit_bill_id, funding_source_id));
        CREATE INDEX ON funding_consumption(billing_account_id);
        CREATE INDEX ON funding_consumption(payment_id);
        CREATE INDEX ON funding_consumption(credit_bill_id);
        CREATE INDEX ON funding_consumption(funding_source_id);

        CREATE TABLE funding_source (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            kind text NOT NULL CHECK (kind IN ('adjustment', 'transfer', 'reversal')),
            billing_account_id uuid NOT NULL,
            currency text NOT NULL,
            action_id uuid NOT NULL REFERENCES balance_action(id),
            consumption_id uuid UNIQUE,
            amount numeric(12,2) NOT NULL CHECK (amount > 0 AND amount <> 'NaN'::numeric),
            available_at timestamptz NOT NULL CHECK (isfinite(available_at)),
            UNIQUE (id, billing_account_id),
            FOREIGN KEY (billing_account_id, currency) REFERENCES billing_account(id, currency),
            FOREIGN KEY (consumption_id, billing_account_id)
                REFERENCES funding_consumption(id, billing_account_id),
            CHECK ((kind = 'reversal') = (consumption_id IS NOT NULL))
        );
        CREATE UNIQUE INDEX funding_source_one_per_action ON funding_source(action_id)
            WHERE kind <> 'reversal';
        CREATE INDEX ON funding_source(billing_account_id);
        ALTER TABLE funding_consumption ADD FOREIGN KEY (funding_source_id, billing_account_id)
            REFERENCES funding_source(id, billing_account_id);
    """)
    for table, check in SOURCE_COLUMN.items():
        op.execute(f"""
            ALTER TABLE {table} ADD COLUMN funding_source_id uuid,
                ADD FOREIGN KEY (funding_source_id, billing_account_id)
                    REFERENCES funding_source(id, billing_account_id),
                DROP CONSTRAINT {check},
                ADD CONSTRAINT {check}
                    CHECK (num_nonnulls(payment_id, credit_bill_id, funding_source_id) = 1);
            CREATE INDEX ON {table}(funding_source_id) WHERE funding_source_id IS NOT NULL;
            COMMENT ON COLUMN {table}.funding_source_id IS
                'Adjustment, received-transfer or reversal source; exclusive with payment_id and credit_bill_id.';
        """)
    op.execute("""
        CREATE UNIQUE INDEX ON funding_hold_source(hold_id, funding_source_id)
            WHERE funding_source_id IS NOT NULL;
        CREATE FUNCTION balance_action_retained() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.status = 'failed' THEN RETURN OLD; END IF;
                RAISE EXCEPTION 'only failed balance actions may be deleted' USING ERRCODE = '23514';
            END IF;
            IF (to_jsonb(NEW) - '{status,reason,description,failure_reason,confirmed_at,cancelled_at}'::text[])
                   <> (to_jsonb(OLD) - '{status,reason,description,failure_reason,confirmed_at,cancelled_at}'::text[])
               OR (OLD.confirmed_at IS NOT NULL AND NEW.confirmed_at IS DISTINCT FROM OLD.confirmed_at)
               OR (OLD.cancelled_at IS NOT NULL AND NEW.cancelled_at IS DISTINCT FROM OLD.cancelled_at)
               OR (OLD.failure_reason IS NOT NULL AND NEW.failure_reason IS DISTINCT FROM OLD.failure_reason)
               OR NOT (NEW.status = OLD.status OR OLD.status = 'created'
                       OR (OLD.status = 'completed' AND NEW.status = 'cancelled'
                           AND OLD.kind IN ('topup', 'adjustment'))) THEN
                RAISE EXCEPTION 'balance action money, references and final status are immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER balance_action_retained BEFORE UPDATE OR DELETE ON balance_action
            FOR EACH ROW EXECUTE FUNCTION balance_action_retained();
        CREATE FUNCTION balance_ledger_retained() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'balance ledger entries are immutable and retained' USING ERRCODE = '23514';
        END $$;
    """)
    for table in ("balance_action_reversal", "funding_consumption", "funding_source"):
        op.execute(
            f"CREATE TRIGGER balance_ledger_retained BEFORE UPDATE OR DELETE ON {table}"
            " FOR EACH ROW EXECUTE FUNCTION balance_ledger_retained()"
        )
    for table in (
        "balance_action",
        "balance_action_reversal",
        "funding_consumption",
        "funding_source",
    ):
        op.execute(
            f"CREATE TRIGGER credit_account_lock BEFORE INSERT{' OR UPDATE' if table == 'balance_action' else ''}"
            f" ON {table} FOR EACH ROW EXECUTE FUNCTION credit_account_lock()"
        )
    op.execute(BOUNDS)
    for table in BOUNDED:
        op.execute(
            f"CREATE CONSTRAINT TRIGGER balance_ledger_bounds AFTER INSERT OR UPDATE ON {table}"
            " DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION balance_ledger_bounds()"
        )
    for table, (summary, columns) in {
        "balance_action": (
            "TMF654 top-up, reservation, adjustment and transfer actions; balanceActionHistory is"
            " their projection. Money and references are immutable; only failed actions may be deleted.",
            {
                "id": "TMF654 action id, shared by its history entry and any reversal.",
                "kind": "topup, reservation, adjustment or transfer (the TMF654 resource).",
                "status": "TMF654 ActionStatusType: created, failed, cancelled or completed.",
                "billing_account_id": "Owning (sender) prepaid account.",
                "currency": "Account and bucket currency.",
                "bucket_id": "TMF654 bucket the action is posted on.",
                "request": "Original checked request document, retained unchanged; money as exact Decimal JSON.",
                "amount": "Exact amount: top-ups/transfers positive, adjustments signed nonzero, holds nonnegative.",
                "transfer_cost": "Exact tax-inclusive transfer fee; zero when absent. Transfers only.",
                "cost_owner": "originator or receiver; who pays transfer_cost. Transfers only.",
                "receiver_bucket_id": "Receiving bucket of a transfer; may be null on a refused transfer.",
                "receiver_billing_account_id": "Receiving prepaid account of a transfer.",
                "receiver_currency": "Receiving bucket currency; equals currency unless the transfer failed.",
                "reason": "TMF654 reason as sent or edited (descriptive).",
                "description": "TMF654 description (descriptive).",
                "failure_reason": "Business refusal reason; set exactly when status is failed.",
                "operator": "Authenticated operator who submitted the action.",
                "requested_at": "Injected-clock receipt instant (requestedDate).",
                "confirmed_at": "Injected-clock completion or failure instant (confirmationDate).",
                "cancelled_at": "Injected-clock cancellation instant.",
                "payment_id": "The done TMF676 payment a top-up records (one top-up per payment).",
                "hold_id": "Funding hold of a reservation or an accepted transfer (principal plus originator fee).",
                "command_id": "Owning order/renewal command of a reservation, or the transfer's worker command.",
            },
        ),
        "balance_action_reversal": (
            "Compensating ledger entry of a cancelled top-up or adjustment; at most one per action.",
            {
                "action_id": "Reversed action; its history id. The primary key allows one reversal.",
                "billing_account_id": "Owning prepaid account.",
                "currency": "Account currency.",
                "amount": "Exact opposite of the reversed amount (ledger entry).",
                "operator": "Operator who cancelled the action.",
                "reversed_at": "Injected-clock reversal instant.",
            },
        ),
        "funding_consumption": (
            "Exact source amounts consumed by debits, outgoing transfer principal and credit reversals.",
            {
                "id": "Immutable consumption identity.",
                "billing_account_id": "Account owning the consumed source.",
                "action_id": "Debit, transfer or reversed action consuming the source.",
                "reversal_action_id": "Set when the consumption belongs to the action's reversal.",
                "payment_id": "Consumed payment; exclusive with the other source columns.",
                "credit_bill_id": "Consumed settled credit note.",
                "funding_source_id": "Consumed adjustment, transfer or reversal source.",
                "amount": "Exact positive amount consumed.",
                "consumed_at": "Injected-clock instant of the consuming commit.",
            },
        ),
        "funding_source": (
            "Credit sources beside payments and credit notes: positive adjustments, received"
            " transfers and restored debit sources. Bounded with allocations, payouts, holds and consumption.",
            {
                "id": "Immutable source identity.",
                "kind": "adjustment, transfer or reversal; funding order follows payments and credit notes.",
                "billing_account_id": "Account the credit belongs to (the receiver for a transfer).",
                "currency": "Account currency.",
                "action_id": "Introducing action (the reversed debit for a reversal source).",
                "consumption_id": "Debit consumption a reversal source restores, exactly once.",
                "amount": "Exact positive credit.",
                "available_at": "Injected-clock commit instant from which the credit is spendable.",
            },
        ),
    }.items():
        op.execute(f"COMMENT ON TABLE {table} IS '{summary.replace("'", "''")}'")
        for column, comment in columns.items():
            op.execute(f"COMMENT ON COLUMN {table}.{column} IS '{comment.replace("'", "''")}'")


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT FROM balance_action) OR EXISTS (SELECT FROM funding_source) THEN
                RAISE EXCEPTION 'balance action and funding source history must be retained at revision 0045';
            END IF;
        END $$;
    """)
    for table in BOUNDED:
        op.execute(f"DROP TRIGGER balance_ledger_bounds ON {table}")
    for table, check in SOURCE_COLUMN.items():
        op.execute(f"""
            ALTER TABLE {table} DROP CONSTRAINT {check}, DROP COLUMN funding_source_id,
                ADD CONSTRAINT {check} CHECK (num_nonnulls(payment_id, credit_bill_id) = 1);
        """)
    op.execute("""
        DROP TABLE funding_consumption CASCADE;
        DROP TABLE funding_source;
        DROP TABLE balance_action_reversal;
        DROP TABLE balance_action;
        DROP FUNCTION balance_ledger_bounds();
        DROP FUNCTION balance_ledger_check(uuid);
        DROP FUNCTION balance_ledger_retained();
        DROP FUNCTION balance_action_retained();
        ALTER TABLE funding_command_hold DROP CONSTRAINT funding_command_hold_identity;
    """)
