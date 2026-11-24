"""The event types of every API: its spec's /listener/<event> operations, PascalCase.

A hub may subscribe to any of them (`eventType=` in its query). Some are never emitted
here - nothing in this service produces them (InformationRequired, JeopardyAlert,
Milestone, ProductBatch, ErrorMessage, billingCycleSpecification changes) - but they
remain valid names. tests/tmf/test_api_hub.py checks this table against spec/.
"""

BASES: dict[str, str] = {
    "TMF639": "/tmf-api/resourceInventory/v5",
    "TMF620": "/tmf-api/productCatalogManagement/v5",
    "TMF622": "/tmf-api/productOrderingManagement/v5",
    "TMF629": "/tmf-api/customer/v5",
    "TMF632": "/tmf-api/partyManagement/v5",
    "TMF637": "/tmf-api/productInventory/v5",
    "TMF666": "/tmf-api/accountManagement/v5",
    "TMF676": "/tmf-api/payment/v4",
    "TMF670": "/tmf-api/paymentMethod/v4",
    "TMF678": "/tmf-api/customerBillManagement/v5",
    "TMF654": "/tmf-api/prepayBalanceManagement/v4",
    "TMF683": "/tmf-api/partyInteractionManagement/v5",
    "TMF621": "/tmf-api/troubleTicket/v5",
}

_CRUD = ("AttributeValueChange", "Create", "Delete", "StateChange")


def _crud(*resources: str) -> tuple[str, ...]:
    return tuple(f"{resource}{kind}Event" for resource in resources for kind in _CRUD)


EVENT_TYPES: dict[str, frozenset[str]] = {
    "TMF639": frozenset(_crud("Resource")),
    "TMF620": frozenset(
        _crud(
            "Category",
            "ProductCatalog",
            "ProductOffering",
            "ProductOfferingPrice",
            "ProductSpecification",
        )
        + (
            "ExportJobCreateEvent",
            "ExportJobStateChangeEvent",
            "ImportJobCreateEvent",
            "ImportJobStateChangeEvent",
        )
    ),
    "TMF622": frozenset(
        {
            "CancelProductOrderCreateEvent",
            "CancelProductOrderInformationRequiredEvent",
            "CancelProductOrderStateChangeEvent",
            "ProductOrderAttributeValueChangeEvent",
            "ProductOrderCreateEvent",
            "ProductOrderDeleteEvent",
            "ProductOrderErrorMessageEvent",
            "ProductOrderInformationRequiredEvent",
            "ProductOrderJeopardyAlertEvent",
            "ProductOrderMilestoneEvent",
            "ProductOrderStateChangeEvent",
        }
    ),
    "TMF629": frozenset(_crud("Customer")),
    "TMF632": frozenset(_crud("Individual", "Organization")),
    "TMF637": frozenset(
        {
            "ProductAttributeValueChangeEvent",
            "ProductCreateEvent",
            "ProductDeleteEvent",
            "ProductProductBatchEvent",
            "ProductStateChangeEvent",
        }
    ),
    "TMF666": frozenset(
        _crud(
            "BillFormat",
            "BillPresentationMedia",
            "BillingAccount",
            "BillingCycleSpecification",
            "FinancialAccount",
            "PartyAccount",
            "SettlementAccount",
        )
    ),
    "TMF676": frozenset(
        {
            "PaymentAttributeValueChangeEvent",
            "PaymentCreateEvent",
            "PaymentDeleteEvent",
            "PaymentStateChangeEvent",
            "RefundAttributeValueChangeEvent",
            "RefundCreateEvent",
            "RefundDeleteEvent",
            "RefundStateChangeEvent",
        }
    ),
    # TMF670 names its state event "StatusChange"; a card's status only changes by removal
    # (a Delete event), so only Create, AttributeValueChange and Delete are sent.
    "TMF670": frozenset(
        {
            "PaymentMethodAttributeValueChangeEvent",
            "PaymentMethodCreateEvent",
            "PaymentMethodDeleteEvent",
            "PaymentMethodStatusChangeEvent",
        }
    ),
    "TMF678": frozenset(
        {
            "CustomerBillCreateEvent",
            "CustomerBillOnDemandCreateEvent",
            "CustomerBillOnDemandStateChangeEvent",
            "CustomerBillStateChangeEvent",
        }
    ),
    # Create, Failure and Cancel of each balance action; a transfer is final, so
    # TransferBalanceCancelEvent is never sent.
    "TMF654": frozenset(
        f"{action}{kind}Event"
        for action in ("TopupBalance", "AdjustBalance", "ReserveBalance", "TransferBalance")
        for kind in ("Create", "Failure", "Cancel")
    ),
    # Both name their state event "StatusChange". Interactions and tickets are records:
    # Delete is never sent, nor InformationRequired; ticket specifications are not
    # implemented (docs/decisions/care.md), so their four events are never sent either.
    "TMF683": frozenset(
        f"PartyInteraction{kind}Event"
        for kind in ("AttributeValueChange", "Create", "Delete", "StatusChange")
    ),
    "TMF621": frozenset(
        {
            "TroubleTicketAttributeValueChangeEvent",
            "TroubleTicketCreateEvent",
            "TroubleTicketDeleteEvent",
            "TroubleTicketInformationRequiredEvent",
            "TroubleTicketResolvedEvent",
            "TroubleTicketSpecificationAttributeValueChangeEvent",
            "TroubleTicketSpecificationCreateEvent",
            "TroubleTicketSpecificationDeleteEvent",
            "TroubleTicketSpecificationStatusChangeEvent",
            "TroubleTicketStatusChangeEvent",
        }
    ),
}

# The v4 APIs: their hub is an EventSubscription and their events keep the v4 notification
# structure (eventId, eventTime, eventType, event; no id, timeOccurred or @type).
V4_APIS = frozenset({"TMF676", "TMF654", "TMF670"})
