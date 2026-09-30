# Guatemala production baseline recovery

The Python/AWS release is commit `ea92daddf6f10fe3b6c56b0b1a27dba70d667ec0`.
A fresh read on 2026-09-30 UTC verified all 194 tracked source files against the
active Elastic Beanstalk source bundle. There were no differences. Production
was Ready/Green and the configuration hash was unchanged. No production writes
were performed.

The GitHub BC extension was version 0.1.8.43. Recovering source from the local
0.1.8.51 compiled package restores the Guatemala controls missing from GitHub:

- Exact-identity accounting cancellation after FEL cancellation.
- Exact-identity repair of a canceled invoice's fiscal series.
- Full credit-memo stamping from the related invoice's certified XML.
- Credit-memo date, tax calculation and original fiscal-series preservation.
- Existing posting/email queue permissions and Mexico exclusion.
- Existing Mexico shipment-date adjustment before stamping.

All 30 AL objects were matched by object identity to source embedded in the
compiled package. The package hash and object hashes are in
`source-provenance.json`. Existing repository formatting is retained where the
source token stream is identical; no business behavior was rewritten.

A fresh GET of production invoiceSync metadata matches all 7 entities,
88 fields and 30 actions, including exact parameter order and types. The
sanitized contract and comparison are included here. Regression tests protect
that contract and the recovered GT source during the Mexico adaptation.

## Verification and remaining release gate

- 140 targeted Python tests pass, covering invoice generation, GT payment terms,
  customer email delivery, webhook routing, MX charge/VAT/ETA policy and the
  recovered production contract.
- The recovered 30-file AL app compiles without diagnostics using the installed
  AL compiler and existing BC symbols.
- Guatemala's Python invoice-generation, payment-term, delivery and webhook code
  is unchanged. Guatemala continues to use BC customer payment terms; the MX ETA
  rule remains isolated to Mexico.
- No invoice was generated, posted, stamped, canceled, sent or modified by this
  verification. No BC app or AWS application was deployed.

**Installed package identity is verified.** After the user granted extension
management access, an authenticated production GET returned installed version
0.1.8.51 and package ID `5eb13e6f-3b29-4a8c-ba9b-085e31d99a03`. This is the exact
NAVX package ID in the archived compiled app used for recovery. All 30 repository
source objects were rechecked against its embedded source. See
`installed-package-verification.json` for identity, hash and method details.

The developer download endpoint still returns HTTP 500, so no fresh package
bytes were downloaded. Verification uses the installed package ID and the
retained compiled package, not API signatures alone. The older .43 package must
not be published over production. This source recovery requires no BC or AWS
redeployment and changes no Python runtime files.

The Mexico adaptation must preserve the recovered GT code, pass these tests,
and complete sandbox cancellation/status/accounting tests before release.
