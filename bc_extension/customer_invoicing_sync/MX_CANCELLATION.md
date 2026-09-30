# Mexico Ocean cancellation adaptation

Status: production canary release 0.1.8.59. The user explicitly authorized direct
production execution on 2026-09-29 instead of the proposed sandbox phase.

The verified Guatemala 0.1.8.51 source is preserved. Before creating a replacement,
`CheckMxReplacementReadiness` validates the original's exact identity, XML,
unapplied balance, native cancellation eligibility, signing configuration and SAT
active status. It performs no BC writes and no fiscal cancellation. Start with
UW-26-ES-002 and hold the remaining three until its end-to-end result is verified.

Sandbox execution was not performed. Local tests and compilation do not prove
PAC or accounting execution; actual production readback is required at each step.
The canary must stop on an identity, total, date, permission or fiscal-state
mismatch and reconcile ambiguous responses before any next mutation.

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

## Operator use after deployment verification

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

## Validation and production canary checks

Python tests exercise read-only default, pending/rejected/unknown holds, UUID and
amount/date mismatches, request timeout, accounting timeout and repeated completion.
The AL test app exercises the actual provider response and SAT XML parsers; compile
it separately; sandbox execution was superseded by the user-authorized production
canary. Compilation alone does not establish execution success. The live run checks credential formats,
legacy stored XML encoding, fresh SAT status queries, concurrent/repeated actions,
payment arriving between preflight and finalization, and native credit application.

Production extension inspection now succeeds after the user updated the existing
application's BC permission assignment. The installed 0.1.8.51 package identity
matches the recovered archive. No extension was deployed during verification.
Do not publish unrelated local TAGOMAGO lifecycle work with this patch.

## Recovered Guatemala baseline

The adaptation now includes the recovered 0.1.8.51 source from merged PR #3.
All seven invoice API entities match a fresh production metadata read. GT
stamping, full credit memo, email and setup source remains unchanged from the
recovered package. The existing MX shipment-date adjustment is also preserved.
The release manifest is 0.1.8.59. Installed baseline verification is complete.
Production deployment and the canary results are recorded separately; this file
does not claim an invoice has been reissued.
