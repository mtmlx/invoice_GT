# ClickUp Customer Sync Scaffold

This project now includes a small ClickUp integration scaffold for the customer-sync work with Business Central.

## What it does

- generates the OAuth authorization URL for your ClickUp app
- exchanges the authorization code for an access token
- optionally waits for the local OAuth callback
- lists authorized ClickUp workspaces
- fetches a task for customer-field mapping review
- fetches the custom fields on a ClickUp list

## Environment variables

Add these to `.env`:

```env
CLICKUP_CLIENT_ID=...
CLICKUP_CLIENT_SECRET=...
CLICKUP_REDIRECT_URI=http://localhost:8000/clickup/oauth/callback
CLICKUP_ACCESS_TOKEN=
CLICKUP_TOKEN_TYPE=Bearer
CLICKUP_DEFAULT_WORKSPACE_ID=
CLICKUP_DEFAULT_CUSTOMER_LIST_ID=
```

## OAuth flow

ClickUp documents the OAuth flow as:

- Authorization URL: `https://app.clickup.com/api`
- Access Token URL: `https://api.clickup.com/api/v2/oauth/token`
- Authorization Code grant

Official docs:

- [Authentication](https://developer.clickup.com/docs/authentication)
- [Get Authorized Workspaces](https://developer.clickup.com/reference/getauthorizedteams)

### Option 1: print the auth URL

```bash
python3 -m clickup_integration.cli auth-url
```

Open the printed URL, authorize the app, then capture the `code` from the redirect URL and exchange it:

```bash
python3 -m clickup_integration.cli exchange-code --code YOUR_CODE --token-output clickup-credentials.json
```

The command writes credentials to a new owner-only (0600) file. Copy its values into `.env` using your local editor; credentials and callback codes are not printed. Existing files and symlinks are rejected before exchanging the code.

### Option 2: let the local callback helper wait for the code

```bash
python3 -m clickup_integration.cli oauth-listen --token-output clickup-credentials.json
```

Then open the authorization URL it prints. After ClickUp redirects to your local callback, the command exchanges the code and saves credentials in the private file. Choose a new file path for each run. A failed exchange may leave an empty file; remove that file locally before retrying.

## Inspect ClickUp structure for customer matching

List authorized workspaces:

```bash
python3 -m clickup_integration.cli workspaces
```

Fetch a task and collapse it into a mapping-friendly shape:

```bash
python3 -m clickup_integration.cli task --task-id 8451352/MTM-2035664
```

Note:

ClickUp task APIs commonly use the task's API ID, not necessarily the human-readable task URL slug. If the human-readable ID does not work, fetch the task from the UI/API using the numeric task ID or inspect the task URL more closely.

Fetch list custom fields:

```bash
python3 -m clickup_integration.cli list-custom-fields --list-id YOUR_LIST_ID
```

## Mapping workflow

1. Fetch the ClickUp task and list custom fields.
2. Compare them against the BC canonical model in [bc_customer_structure.md](/Users/mario/Documents/Contracting_Tool/docs/bc_customer_structure.md).
3. Choose the match key.
4. Build a deterministic upsert flow into BC customers.

## Initial recommendation

- Keep BC as the integration target structure for customer records.
- Use one shared canonical model for MX and GT.
- Route to the correct BC company using an explicit `market` field.
- Keep currency explicit because both MX and GT may invoice in local currency or USD.
