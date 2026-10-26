# Guatemala invoice customer resolution

The shipment invoice path resolves customers in this order:

1. Preserve an explicit shipment Business Central Customer ID or Number.
2. For Guatemala, read the single task in `Consignee’s relationship to CRM`
   (field `5e1637c2-6bb2-4428-a592-06477fd13ef1`). Use its BC customer ID when
   its `Clientes/` label, legal name, or task name exactly matches the shipment's
   `Invoice to (Consignee's name)` after whitespace/case normalization.
3. Retain the existing BC name/email/website resolver when no applicable linked
   identity is available, including when the shipment bills a third party.

Read the BC ID in the shipment's market/company, verify the returned ID and
customer number, and compare both CRM tax fields when present. Punctuation in
NITs is ignored; letters such as a check digit K are retained. Conflicting
identifiers, multiple linked tasks, unreadable records, or an invalid UUID block
preparation. No alias or customer number is hardcoded. Mexico retains its
existing resolution. Payment terms, currency/FEL readiness, charge mappings,
customer split rules, duplicate checks, issuance, and delivery retain their
existing paths.

## Read-only validation

With the normal production runtime configuration present in the process:

```bash
python -m scripts.validate_linked_crm_invoice_customer --task-id 86e27yzwp
```

This script reads the real task and CRM record, simulates invoice readiness on a
deep copy, and calls only the preview path. It rejects every HTTP business write;
only GET and Microsoft OAuth token acquisition are permitted. It compares the
source ClickUp task and BC invoices before and after validation. It never calls
the production webhook or issue/apply, stamping, or delivery functions.

Validation on 2026-10-26 resolved MTMLXGT-31160 / SolarGuat via CRM task
`86dxvmj26` to BC ID `c12adf07-6bea-f011-8542-002248a99c7e`, customer `C00086`,
NIT `79259146`. Totals reconciled: INT USD 6,308 + NAT USD 925 = USD 7,233.
The preview returned `duplicate_invoice` for GTFVR0005222 / GTFVR0005223.
There were zero business write requests or blocked-write attempts; source task
and invoice records were unchanged. The full Python suite passed 512 tests.

Release base: `e917cc1564da9c873b6084e7686e082cd3d47e0b`, matching every
tracked file in the active `gt-priority-obs-containers-20261009-e917cc1` package.
Deploy only the committed archive to `mtm-gt-invoice-webhook-prod`, preserve its
environment settings, read back the EB version and health/readiness endpoints,
and repeat read-only validation using code retrieved from the new S3 package.
