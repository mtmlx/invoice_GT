# Mexico Ocean cancellation adaptation

Status: implementation candidate. No production cancellation, posting, stamping,
email, ClickUp mutation or extension deployment was performed while building it.

This patch applies to the clean GitHub release `ea92dad`. Do **not** install the
whole compiled 0.1.8.51 package over Production: the installed extension source and
version have not been established, and live metadata contains newer Guatemala
actions absent from that baseline. Merge these targeted changes onto the verified
installed source, retain its GT and TAGOMAGO warehouse controls, assign the next
version, compile, and validate in a sandbox before a production upgrade.

## Contract

- `RequestMxCancellation` on `postedInvoiceFelDescriptions` accepts a complete
  expected pair: replacement invoice number, original and replacement UUIDs,
  external document number, total including VAT and replacement due date.
- This initial controlled route accepts only `MTM_MX_PROD`, sell-to and bill-to
  `C00067`, USD, matching shipment references and totals, Ocean Freight plus an
  allowlist of Ocean items, INT VAT 0 and NAT VAT 16. Warehouse/distribution items
  remain excluded. This supports the four reviewed invoices whose totals stay
  unchanged; it deliberately does not support arbitrary amount-changing reissues.
- Both stamped XML documents are checked against header UUID, issuer/recipient
  RFC, currency, subtotal, VAT and total. Replacement XML must contain relation 04
  to exactly the original UUID. It must be active and have the approved due date.
- The original must have exactly one open, fully unapplied customer entry and
  pass BC's native cancellation eligibility check.
- SAT must report both original and replacement active before the first request.
- A durable `mxCancellations` entry is committed **before** the cancellation POST.
  Its original/replacement pair cannot be changed. Repeating the same request
  returns without resubmitting, even after rejection or timeout. A crash before
  transmission can therefore leave Unknown; deliberate manual reconciliation is
  required rather than guessing whether the PAC received it.
- Provider response acceptance becomes Pending, including immediate `Cancelado`.
  A malformed response or transport failure stays Unknown. Explicit provider
  rejection becomes Rejected. None reverses accounting.
- `RefreshStatus` calls the official SAT Consulta SOAP service, which only reads
  fiscal state. It does not repeat the PAC cancellation request.
- `FinalizeAccounting` freshly verifies replacement active and original canceled
  at SAT, commits that evidence, then performs the native BC accounting reversal.
  It verifies the original and corrective credit memo are both closed with zero
  remaining balances and matching customer/currency/amount, and stores the credit
  memo number. A separate committed accounting-attempt marker prevents blind retry
  after an ambiguous BC failure. Re-entry recovers a verified completed reversal.
- Audit records and evidence BLOBs are API read-only; writes occur inside guarded
  AL actions. No signing material or raw provider response is returned to clients.
- The previous combined `CancelMxInvoiceWithSubstitution` action now refuses to
  run. The native Posted Sales Invoice `CancelInvoice` and `Cancela MX` actions
  refuse C00067 so operators use the controlled path.

## Provider prerequisites

The adapter uses the existing HTTPS PAC URL, CSD certificate/private key/password
and an already-configured token from BC General Ledger Setup. It does not extract
credentials to Python, log payloads, rotate credentials, or call the legacy
`CancelaFactura` implementation. Missing/expired credentials block the operation;
there is no automatic cancellation resubmission after authentication repair.

SAT status contract:
https://wwwmat.sat.gob.mx/cs/Satellite?blobcol=urldata&blobkey=id&blobtable=MungoBlobs&blobwhere=1461175013197&ssbinary=true

## Operator use after deployment validation

The replacement must already have been issued, stamped and reviewed. This command
does not create, stamp or deliver it. Supply the exact new invoice/UUID rather than
an external-reference lookup that could select the original.

Create a private JSON plan with `invoice_number`, `replacement_number`,
`original_uuid`, `replacement_uuid`, `external_document_number`,
`amount_including_vat` (decimal string) and `replacement_due_date` (ISO date).

From the repository root with the approved BC configuration:

```sh
python -m scripts.advance_mx_cancellation --plan /absolute/path/approved-pair.json
python -m scripts.advance_mx_cancellation --plan /absolute/path/approved-pair.json --apply
python -m scripts.advance_mx_cancellation --plan /absolute/path/approved-pair.json --apply --finalize-accounting
```

The first command is read-only. A missing API means the BC adaptation has not been
deployed; do not fall back to the retired endpoint or native cancellation buttons.
Pending/rejected/unknown states must not be called complete. The client never
loops or silently retries a mutation. Resume checks the existing BC operation.

Start with B0003376 / UW-26-ES-002. Before any customer delivery or ClickUp update,
verify replacement XML/PDF, due date, INT/NAT charges, original fiscal cancellation,
corrective credit application, original zero balance and replacement outstanding
balance. Re-read ETA and terms when preparing the new invoice. Then repeat for the
remaining three after the first pair passes verification.

## Validation and remaining release checks

Python tests exercise read-only default, pending/rejected/unknown holds, UUID and
amount/date mismatches, request timeout, accounting timeout and repeated completion.
The AL test app exercises the actual provider response and SAT XML parsers; compile
it separately and run codeunit 71940 in a BC sandbox. Compilation alone does not
establish execution success. A sandbox PAC test must validate credential formats,
legacy stored XML encoding, fresh SAT status queries, concurrent/repeated actions,
payment arriving between preflight and finalization, and native credit application.

Production version/permission inspection is currently blocked for the configured
API identity. A signed-in administrator can read Extension Management; no access
permissions were expanded as part of this change. Do not overwrite newer installed
functionality or publish unrelated local TAGOMAGO lifecycle work with this patch.

## Recovered Guatemala baseline

The adaptation now includes the recovered 0.1.8.51 source from draft PR #3.
All seven invoice API entities match a fresh production metadata read. GT
stamping, full credit memo, email and setup source remains unchanged from the
recovered package. The existing MX shipment-date adjustment is also preserved.
The manifest stays at the recovered version for review only; assign a new version
after confirming the installed package and before any release. The installed
version/source confirmation and sandbox execution gates still apply.
