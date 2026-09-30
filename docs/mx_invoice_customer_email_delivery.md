# Mexico invoice delivery through the shared BC email routine

Mexico reuses Guatemala's SMTP application authentication, `MTM Invoice Customer
Delivery` scenario, native Email submission and message/account verification.
Guatemala continues to use Consuelo and the `FacturaGTM` report. Mexico selects
Carlos and the exact posted invoice's stamped PAC PDF and normalized CFDI XML.
Mexico manual posting capture remains excluded.

## Independent activation

The Guatemala environment variable remains `CLICKUP_INVOICE_SEND_ENABLED`.
Mexico uses `CLICKUP_MX_INVOICE_SEND_ENABLED`, which defaults to false even when
the Guatemala gate is true. Enabling the Mexico gate does not enable issuance,
manual posting capture, or the restricted TAGOMAGO warehouse/distribution path.
The existing approved TAGOMAGO USD Ocean provenance check remains required.

Do not enable customer delivery until the installed BC extension exposes the
native Mexico capability, Carlos has mailbox-scoped application authorization,
and one internal canary has verified the intended sender and both attachments.

## Persisted invoice-specific delivery intent

The `PrepareInvoiceEmailDelivery` service action does not send email. It binds:

- Current posted fiscal UUID and external reference.
- Exact amount including VAT and current due date.
- Customer invoice recipients resolved in BC, plus the explicitly approved CC.
- Validated PAC PDF and normalized CFDI XML SHA256 values.

Preparation also requires `expectedPdfSha256` from a PAC PDF snapshot that an
operator independently reviewed against the invoice's identity and totals. The
current BC blob's own hash cannot be used as independent proof of its contents.

The customer send action requires this prepared intent. The Python bridge
re-reads the posted invoice and fiscal API row, requires
`MX_PAC_PDF_CFDI_XML_V1` capability with validated attachments and an already
prepared audit. Unprepared Mexico invoices are held for operator review. The
bridge does not prepare new intent from the current blob's own hash. A caller
may provide `cc_recipients_by_invoice` to verify prepared CC for selected
invoices. There is no global Mario CC or reference-based automatic CC rule.

For UW-26-ES-002, prepare the approved replacement B0003383 explicitly with
`mario@mtmlogix.com` as CC. The action must receive the verified current UUID,
external reference, amount, due date and independently reviewed PAC PDF hash.
Never apply that CC to another invoice
by changing a shared environment variable or customer master record.

After sending, the bridge requires Carlos as sender, a native BC message ID,
`nativeSentVerified=true`, the unchanged prepared CC, and UUID/PDF/XML evidence.
A client timeout results in an audit readback; no second send is submitted.
Incomplete or ambiguous evidence holds the delivery for operator review.

## Internal canary

The existing command retains Guatemala as its default market. Mexico requires an
explicit existing invoice number:

```text
python scripts/send_gt_invoice_email_canary_once.py \
  --market MX --invoice-number B0003383 \
  --expected-pdf-sha256 <reviewed-pac-pdf-sha256> --env-file <connection-file>
```

The command is read-only unless `--apply` is included. The internal recipient is
fixed to Mario, and Mexico's separate `SendApprovedMxInvoiceTestEmailToMario`
action compares the supplied reviewed PDF hash before sending. The canary does
not prepare or consume the customer delivery
audit. It requires native readiness and checks native canary evidence before
sending. Missing evidence, a wrong sender/account, queued or ambiguous messages
and already sent messages cannot cause another send. An explicit failed outbox
record requires controlled operator retry after its recorded cause is fixed.

Use the existing SMTP authentication-only preflight with Carlos's mailbox before
the canary. Reuse the application credential securely and add a separate exact
Carlos mailbox scope; preserve Consuelo's role assignment and sender setup.

## Carlos account setup in BC

In `MTM_MX_PROD`, edit the existing `Carlos - Mexico` SMTP account:

- Email address: `carlos@mtmlogix.com`.
- Server: `smtp.office365.com`, secure connection enabled.
- Authentication: OAuth 2.0, with **Use custom OAuth settings** enabled.
- Client ID: `ea5a14b8-e008-49e5-8190-bbf37d50aeb5`
  (`MTM BC Invoice SMTP Sender`, the existing Guatemala SMTP app).
- Tenant ID: `c4f30f62-18b6-4bdd-b9cd-ace99f865e49`.
- Client secret: enter the existing valid app secret from secure storage directly
  in BC. Never put the secret in chat, source control, or release evidence.

Assign the `MTM Invoice Customer Delivery` email scenario to this account in the
Mexico company. The scenario is sufficient; changing the default sender is not
required. Guatemala's account, scenario assignment and credentials stay intact.

Exchange authorization is managed separately with
`scripts/configure_mx_smtp_scope.ps1`. Its default mode is read-only; `-Apply`
adds an exact Carlos mailbox scope to the existing SMTP app and enables SMTP
submission only for that mailbox. It verifies Consuelo and tenant SMTP settings
remain unchanged. It does not configure BC credentials or send email.
