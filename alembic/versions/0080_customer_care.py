"""Customer care: interaction notes (TMF683) and cases (TMF621), linked to the audit log (#199).

Revision ID: 0080
Revises: 0079

docs/decisions/care.md. Seven tables; notes and a ticket's status history are append-only
(triggers). Customer, product and billing account are foreign keys; a reference to an
`account_audit` entry has none (the log outlives what it describes). The audit log takes four
new actions for care work.
"""

from alembic import op

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None

CHANNELS = "'phone', 'chat', 'email', 'store', 'other'"


def upgrade() -> None:
    op.execute(f"""
        CREATE TYPE trouble_ticket_status_type AS ENUM (
            'acknowledged', 'rejected', 'pending', 'held', 'inProgress', 'cancelled', 'closed',
            'resolved');

        CREATE TABLE party_interaction (
            id uuid PRIMARY KEY,
            customer_id uuid NOT NULL REFERENCES customer(id),
            direction text NOT NULL CHECK (direction IN ('inbound', 'outbound')),
            reason text NOT NULL CHECK (reason <> ''),
            description text,
            channel text NOT NULL CHECK (channel IN ({CHANNELS})),
            channel_role text NOT NULL CHECK (channel_role <> ''),
            status text NOT NULL CHECK (status IN ('open', 'inProgress', 'completed')),
            status_change_date timestamptz NOT NULL,
            interaction_start timestamptz,
            interaction_end timestamptz,
            created_by text NOT NULL CHECK (created_by <> ''),
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            base_type text CHECK (base_type <> ''),
            schema_location text CHECK (schema_location <> ''),
            CHECK (interaction_end IS NULL OR interaction_start IS NOT NULL),
            CHECK (interaction_end IS NULL OR interaction_end > interaction_start)
        );
        CREATE INDEX party_interaction_customer_idx
            ON party_interaction (customer_id, created_at DESC, id DESC);
        CREATE INDEX party_interaction_created_idx ON party_interaction (created_at DESC, id DESC);
        COMMENT ON TABLE party_interaction IS
            'One recorded contact with a customer (TMF683 PartyInteraction): a call, chat, email or store visit with its notes. A record: never deleted.';
        COMMENT ON COLUMN party_interaction.customer_id IS 'The customer the contact was with (relatedParty, role customer).';
        COMMENT ON COLUMN party_interaction.direction IS 'inbound (the customer contacted us) or outbound.';
        COMMENT ON COLUMN party_interaction.reason IS 'Why the contact happened, free text.';
        COMMENT ON COLUMN party_interaction.channel IS 'phone, chat, email, store or other (relatedChannel).';
        COMMENT ON COLUMN party_interaction.channel_role IS 'The role of the channel, as sent (relatedChannel.role).';
        COMMENT ON COLUMN party_interaction.status IS 'open, inProgress or completed; a completed interaction only takes notes.';
        COMMENT ON COLUMN party_interaction.interaction_start IS 'interactionDate.startDateTime, as sent.';
        COMMENT ON COLUMN party_interaction.interaction_end IS 'interactionDate.endDateTime, as sent (exclusive).';
        COMMENT ON COLUMN party_interaction.created_by IS 'The operator who logged it.';

        CREATE TABLE trouble_ticket (
            id uuid PRIMARY KEY,
            customer_id uuid NOT NULL REFERENCES customer(id),
            name text CHECK (name <> ''),
            description text NOT NULL CHECK (description <> ''),
            severity text NOT NULL CHECK (severity IN ('minor', 'major', 'critical')),
            ticket_type text NOT NULL CHECK (ticket_type IN ('incident', 'complaint', 'request')),
            priority text NOT NULL DEFAULT 'medium'
                CHECK (priority IN ('low', 'medium', 'high', 'critical')),
            channel text CHECK (channel IN ({CHANNELS})),
            requested_resolution_date timestamptz,
            expected_resolution_date timestamptz,
            resolution_date timestamptz,
            status trouble_ticket_status_type NOT NULL DEFAULT 'acknowledged',
            status_change_date timestamptz NOT NULL,
            status_change_reason text CHECK (status_change_reason <> ''),
            created_by text NOT NULL CHECK (created_by <> ''),
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            base_type text CHECK (base_type <> ''),
            schema_location text CHECK (schema_location <> ''),
            CHECK ((status = 'resolved' OR status = 'closed') = (resolution_date IS NOT NULL))
        );
        CREATE INDEX trouble_ticket_customer_idx
            ON trouble_ticket (customer_id, created_at DESC, id DESC);
        CREATE INDEX trouble_ticket_created_idx ON trouble_ticket (created_at DESC, id DESC);
        CREATE INDEX trouble_ticket_status_idx
            ON trouble_ticket (status, created_at DESC, id DESC);
        COMMENT ON TABLE trouble_ticket IS
            'A customer case (TMF621 TroubleTicket): a simple ticket with a status. A record: never deleted.';
        COMMENT ON COLUMN trouble_ticket.customer_id IS 'The customer the case belongs to (relatedParty, role customer).';
        COMMENT ON COLUMN trouble_ticket.severity IS 'minor, major or critical.';
        COMMENT ON COLUMN trouble_ticket.ticket_type IS 'incident, complaint or request.';
        COMMENT ON COLUMN trouble_ticket.priority IS 'low, medium (the default), high or critical.';
        COMMENT ON COLUMN trouble_ticket.channel IS 'The channel the case came through: phone, chat, email, store or other.';
        COMMENT ON COLUMN trouble_ticket.resolution_date IS 'When it was resolved; cleared when a resolved ticket is reopened.';
        COMMENT ON COLUMN trouble_ticket.status IS 'TMF621 TroubleTicketStatusType; created acknowledged. closed, rejected and cancelled are final.';
        COMMENT ON COLUMN trouble_ticket.status_change_reason IS 'The reason given with the last status change.';
        COMMENT ON COLUMN trouble_ticket.created_by IS 'The operator who opened it.';

        CREATE TABLE party_interaction_item (
            id uuid PRIMARY KEY,
            interaction_id uuid NOT NULL REFERENCES party_interaction(id),
            position integer NOT NULL CHECK (position >= 0),
            item_id text NOT NULL CHECK (item_id <> ''),
            reason text,
            resolution text,
            item_type text,
            role text NOT NULL CHECK (role <> ''),
            entity_type text NOT NULL
                CHECK (entity_type IN ('Product', 'BillingAccount', 'TroubleTicket', 'AuditEntry')),
            product_id uuid REFERENCES product(id),
            billing_account_id uuid REFERENCES billing_account(id),
            trouble_ticket_id uuid REFERENCES trouble_ticket(id),
            audit_id uuid,
            UNIQUE (interaction_id, position),
            UNIQUE (interaction_id, item_id),
            CHECK (num_nonnulls(product_id, billing_account_id, trouble_ticket_id, audit_id) = 1),
            CHECK ((entity_type = 'Product') = (product_id IS NOT NULL)),
            CHECK ((entity_type = 'BillingAccount') = (billing_account_id IS NOT NULL)),
            CHECK ((entity_type = 'TroubleTicket') = (trouble_ticket_id IS NOT NULL)),
            CHECK ((entity_type = 'AuditEntry') = (audit_id IS NOT NULL))
        );
        CREATE INDEX party_interaction_item_product_idx
            ON party_interaction_item (product_id) WHERE product_id IS NOT NULL;
        CREATE INDEX party_interaction_item_account_idx
            ON party_interaction_item (billing_account_id) WHERE billing_account_id IS NOT NULL;
        CREATE INDEX party_interaction_item_ticket_idx
            ON party_interaction_item (trouble_ticket_id) WHERE trouble_ticket_id IS NOT NULL;
        COMMENT ON TABLE party_interaction_item IS
            'What a contact was about (TMF683 interactionItem): a product (the line), a billing account, a trouble ticket or an audit log entry.';
        COMMENT ON COLUMN party_interaction_item.position IS 'The order the items were sent in.';
        COMMENT ON COLUMN party_interaction_item.item_id IS 'The item id: the client''s, else its number in the interaction.';
        COMMENT ON COLUMN party_interaction_item.item_type IS 'interactionItemType, as sent.';
        COMMENT ON COLUMN party_interaction_item.role IS 'The role of the entity in the item, as sent (item.role).';
        COMMENT ON COLUMN party_interaction_item.entity_type IS 'Which of the four references is set.';
        COMMENT ON COLUMN party_interaction_item.audit_id IS 'An account_audit entry; no foreign key (the log outlives what it describes).';

        CREATE TABLE party_interaction_note (
            id uuid PRIMARY KEY,
            interaction_id uuid NOT NULL REFERENCES party_interaction(id),
            seq bigint GENERATED ALWAYS AS IDENTITY,
            author text NOT NULL CHECK (author <> ''),
            at timestamptz NOT NULL,
            text text NOT NULL CHECK (text <> '')
        );
        CREATE INDEX party_interaction_note_idx ON party_interaction_note (interaction_id, seq);
        COMMENT ON TABLE party_interaction_note IS
            'Notes of an interaction (TMF683 note): append-only, the author is the operator and the date the clock.';
        COMMENT ON COLUMN party_interaction_note.seq IS 'Insertion order.';

        CREATE TABLE trouble_ticket_related_entity (
            id uuid PRIMARY KEY,
            ticket_id uuid NOT NULL REFERENCES trouble_ticket(id),
            position integer NOT NULL CHECK (position >= 0),
            role text NOT NULL CHECK (role <> ''),
            entity_type text NOT NULL CHECK (
                entity_type IN ('Product', 'BillingAccount', 'PartyInteraction', 'AuditEntry')),
            product_id uuid REFERENCES product(id),
            billing_account_id uuid REFERENCES billing_account(id),
            party_interaction_id uuid REFERENCES party_interaction(id),
            audit_id uuid,
            UNIQUE (ticket_id, position),
            CHECK (num_nonnulls(product_id, billing_account_id, party_interaction_id, audit_id) = 1),
            CHECK ((entity_type = 'Product') = (product_id IS NOT NULL)),
            CHECK ((entity_type = 'BillingAccount') = (billing_account_id IS NOT NULL)),
            CHECK ((entity_type = 'PartyInteraction') = (party_interaction_id IS NOT NULL)),
            CHECK ((entity_type = 'AuditEntry') = (audit_id IS NOT NULL))
        );
        CREATE INDEX trouble_ticket_related_product_idx
            ON trouble_ticket_related_entity (product_id) WHERE product_id IS NOT NULL;
        CREATE INDEX trouble_ticket_related_account_idx
            ON trouble_ticket_related_entity (billing_account_id)
            WHERE billing_account_id IS NOT NULL;
        CREATE INDEX trouble_ticket_related_interaction_idx
            ON trouble_ticket_related_entity (party_interaction_id)
            WHERE party_interaction_id IS NOT NULL;
        COMMENT ON TABLE trouble_ticket_related_entity IS
            'What a case is about (TMF621 relatedEntity): a product (the line), a billing account, a party interaction or an audit log entry.';
        COMMENT ON COLUMN trouble_ticket_related_entity.position IS 'The order the entities were sent in.';
        COMMENT ON COLUMN trouble_ticket_related_entity.role IS 'The role of the entity, as sent.';
        COMMENT ON COLUMN trouble_ticket_related_entity.entity_type IS 'Which of the four references is set.';
        COMMENT ON COLUMN trouble_ticket_related_entity.audit_id IS 'An account_audit entry; no foreign key (the log outlives what it describes).';

        CREATE TABLE trouble_ticket_note (
            id uuid PRIMARY KEY,
            ticket_id uuid NOT NULL REFERENCES trouble_ticket(id),
            seq bigint GENERATED ALWAYS AS IDENTITY,
            author text NOT NULL CHECK (author <> ''),
            at timestamptz NOT NULL,
            text text NOT NULL CHECK (text <> '')
        );
        CREATE INDEX trouble_ticket_note_idx ON trouble_ticket_note (ticket_id, seq);
        COMMENT ON TABLE trouble_ticket_note IS
            'Notes of a case (TMF621 note): append-only, the author is the operator and the date the clock.';
        COMMENT ON COLUMN trouble_ticket_note.seq IS 'Insertion order.';

        CREATE TABLE trouble_ticket_status_change (
            id uuid PRIMARY KEY,
            ticket_id uuid NOT NULL REFERENCES trouble_ticket(id),
            seq bigint GENERATED ALWAYS AS IDENTITY,
            status trouble_ticket_status_type NOT NULL,
            at timestamptz NOT NULL,
            reason text CHECK (reason <> ''),
            operator text NOT NULL CHECK (operator <> '')
        );
        CREATE INDEX trouble_ticket_status_change_idx
            ON trouble_ticket_status_change (ticket_id, seq);
        COMMENT ON TABLE trouble_ticket_status_change IS
            'Every status a case had (TMF621 statusChangeHistory), the first being acknowledged: append-only.';
        COMMENT ON COLUMN trouble_ticket_status_change.seq IS 'Insertion order.';
        COMMENT ON COLUMN trouble_ticket_status_change.reason IS 'statusChangeReason, as given with the change.';
        COMMENT ON COLUMN trouble_ticket_status_change.operator IS 'Who changed the status.';

        CREATE FUNCTION care_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END $$;
        CREATE TRIGGER party_interaction_note_immutable
            BEFORE UPDATE OR DELETE ON party_interaction_note
            FOR EACH ROW EXECUTE FUNCTION care_append_only();
        CREATE TRIGGER trouble_ticket_note_immutable
            BEFORE UPDATE OR DELETE ON trouble_ticket_note
            FOR EACH ROW EXECUTE FUNCTION care_append_only();
        CREATE TRIGGER trouble_ticket_status_change_immutable
            BEFORE UPDATE OR DELETE ON trouble_ticket_status_change
            FOR EACH ROW EXECUTE FUNCTION care_append_only();

        ALTER TABLE account_audit DROP CONSTRAINT account_audit_action_check;
        ALTER TABLE account_audit ADD CONSTRAINT account_audit_action_check CHECK (action IN (
            'sim_swap', 'number_change', 'number_transfer_pin', 'contact_change',
            'port_out', 'pin_set', 'pin_reset', 'lock_set', 'lock_cleared',
            'pin_lockout', 'interaction_logged', 'ticket_opened', 'ticket_status_changed',
            'care_note_added'));
        COMMENT ON COLUMN account_audit.action IS 'sim_swap, number_change, number_transfer_pin, contact_change, port_out, pin_set, pin_reset, lock_set, lock_cleared, pin_lockout, and the care work of issue 199: interaction_logged, ticket_opened, ticket_status_changed, care_note_added.';
        COMMENT ON COLUMN account_audit.detail IS 'What the action touched (order, SIM, number, fields changed; the interaction or ticket of a care entry), and for a contact change the addresses the alert goes to. No secrets.';
    """)


def downgrade() -> None:
    # a rollback discards the interactions and the cases; care entries of the audit log
    # (append-only for the application) are removed with them, or the old CHECK would not hold
    op.execute("""
        ALTER TABLE account_audit DISABLE TRIGGER account_audit_immutable;
        DELETE FROM account_audit WHERE action IN (
            'interaction_logged', 'ticket_opened', 'ticket_status_changed', 'care_note_added');
        ALTER TABLE account_audit ENABLE TRIGGER account_audit_immutable;
        ALTER TABLE account_audit DROP CONSTRAINT account_audit_action_check;
        ALTER TABLE account_audit ADD CONSTRAINT account_audit_action_check CHECK (action IN (
            'sim_swap', 'number_change', 'number_transfer_pin', 'contact_change',
            'port_out', 'pin_set', 'pin_reset', 'lock_set', 'lock_cleared',
            'pin_lockout'));
        COMMENT ON COLUMN account_audit.action IS 'sim_swap, number_change, number_transfer_pin, contact_change, port_out, pin_set, pin_reset, lock_set, lock_cleared, pin_lockout.';
        COMMENT ON COLUMN account_audit.detail IS 'What the action touched (order, SIM, number, fields changed), and for a contact change the addresses the alert goes to. No secrets.';
        DROP TABLE trouble_ticket_status_change;
        DROP TABLE trouble_ticket_note;
        DROP TABLE trouble_ticket_related_entity;
        DROP TABLE party_interaction_note;
        DROP TABLE party_interaction_item;
        DROP TABLE trouble_ticket;
        DROP TABLE party_interaction;
        DROP FUNCTION care_append_only();
        DROP TYPE trouble_ticket_status_type;
    """)
