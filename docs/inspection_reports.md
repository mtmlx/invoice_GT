# Truck Inspection PDF Reports

This workflow generates physical inspection PDF reports for truck shipment tasks.
It pulls task metadata from ClickUp, matches inspection photos from SharePoint,
uploads the finished report back to SharePoint, and writes the SharePoint report
link back to a ClickUp custom field.

## Current First Slice

- Python package: `inspection_reports`
- CLI: `python3 -m inspection_reports.cli`
- Local PDF output: `output/pdf`
- Temporary image downloads: `tmp/inspection_reports`

The ClickUp discovery pass found the likely operating area:

- Space: `Magna Space`
- Folder/project: `Magna Motors`
- Coordination task: `MTLXMGN-186` / `86dzb0pdd`
- List seen on that task: `901709884131` (`Authority Matrix & RASCI`)

That coordination task may not be the per-truck list. Before running batch
automation, set `INSPECTION_REPORT_CLICKUP_LIST_ID` to the list that contains one
task per truck.

## Required Setup

Set these variables in `.env`:

```env
INSPECTION_REPORT_CLICKUP_LIST_ID=
INSPECTION_REPORT_SHAREPOINT_HOSTNAME=mtmlogixmx.sharepoint.com
INSPECTION_REPORT_SHAREPOINT_SITE_PATH=/sites/Magna
INSPECTION_REPORT_SOURCE_FOLDER_URL=
INSPECTION_REPORT_OUTPUT_FOLDER_URL=
INSPECTION_REPORT_SOURCE_FOLDER_PATH=Inspection Photos
INSPECTION_REPORT_OUTPUT_FOLDER_PATH=Inspection Reports
INSPECTION_REPORT_LINK_FIELD_IDS=
INSPECTION_REPORT_LINK_FIELD_NAMES=Inspection Report Link,Inspection Report,Physical Inspection Report
INSPECTION_REPORT_ATTACHMENT_FIELD_IDS=3a454367-98b8-4a8f-96d5-6f8872b1ada1
INSPECTION_REPORT_ATTACHMENT_FIELD_NAMES=Origin Inspection Report
INSPECTION_REPORT_ALLOWED_STATUSES=passed
INSPECTION_REPORT_TEMPLATE_MAX_IMAGES_PER_SECTION=4
INSPECTION_REPORT_USE_LEGACY_DOCX_TEMPLATE=false
INSPECTION_REPORT_COMMENT_FIELD_IDS=
INSPECTION_REPORT_COMMENT_FIELD_NAMES=Inspection AI Exec Summary
```

Microsoft Graph credentials are also required for SharePoint download/upload:

```env
GRAPH_TENANT_ID=
GRAPH_CLIENT_ID=
GRAPH_CLIENT_SECRET=
```

If the existing Business Central Entra app has the correct Graph permissions, the
code can fall back to `BC_TENANT_ID`, `BC_CLIENT_ID`, and `BC_CLIENT_SECRET`.
The app needs SharePoint read/write access, typically `Sites.ReadWrite.All` or a
site-scoped permission model approved by IT.

## Matching Rules

The workflow supports two photo-location patterns:

1. A ClickUp task has a field such as `Inspection Photos Folder` or
   `Inspection Photos Link`. In that case, all supported images in that folder
   are used for that task. SharePoint folder sharing URLs are supported.
2. No task-specific folder field exists. The workflow scans
   `INSPECTION_REPORT_SOURCE_FOLDER_URL` or `INSPECTION_REPORT_SOURCE_FOLDER_PATH`
   and matches image paths/names against configured identifiers such as `VIN`,
   `Chassis Number`, `Unit Number`, `Truck Number`, `Booking number/`, and
   `MTM Booking`.

Supported image extensions default to `.jpg`, `.jpeg`, `.png`, and `.webp`.

Batch runs default to tasks whose ClickUp status is `passed`. To include multiple
statuses, set `INSPECTION_REPORT_ALLOWED_STATUSES` as a comma-separated list.
Set it blank only when you intentionally want all statuses.

The report metadata is also an allowlist. `INSPECTION_REPORT_FIELD_NAMES`
controls which ClickUp custom fields are extracted into the report, so keep it
limited to the fields that matter for the inspection PDF.

The default output is the print-first MTM Logix Command Era `Inspection Report`:
US Letter portrait, Noto Sans, the approved MTM Logix command signature, a
vehicle summary, checkpoint status indicators, and adaptive photo evidence pages.
The Word-template path is retained only for rollback; set
`INSPECTION_REPORT_USE_LEGACY_DOCX_TEMPLATE=true` to use it intentionally.

For the Magna Inspections list, the current allowlist is the inspection-specific
field set: vehicle attributes, checkpoint results/comments, report source URLs,
inspection cost fields, and final report URL fields. Image matching uses
`VIN number`, and task-specific image folders use `OneDrive Pictures`.
Completion runs also write the resolved picture folder URL back to
`INSPECTION_REPORT_PICTURE_FOLDER_FIELD_IDS`, currently
`1bfac08a-6df3-4ea4-a581-5f4b41a97bb1`, before changing a task to `PASSED`.
Live report runs also upload the generated PDF into the ClickUp Files custom
field configured by `INSPECTION_REPORT_ATTACHMENT_FIELD_IDS`, currently
`3a454367-98b8-4a8f-96d5-6f8872b1ada1` (`Origin Inspection Report`).
Photo sections are generated dynamically from the SharePoint folder structure:
each image folder becomes its own section, with up to
`INSPECTION_REPORT_TEMPLATE_MAX_IMAGES_PER_SECTION` photos per folder. The
general comments panel uses
`INSPECTION_REPORT_COMMENT_FIELD_IDS` or `INSPECTION_REPORT_COMMENT_FIELD_NAMES`
when available, then falls back to `Inspection Result Summary`.

Image folders are section-aware. The default section-folder prefixes support
both zero-padded hyphen folders such as `02-General360` and dotted folders such
as `2.General360° Overview`:

- `Car Information` -> `01-Car Information`, `1.Car Information`
- `General / 360° Overview` -> `02-General360`, `2.General360`
- `Corrosion Details` -> `03-Corrosion`, `3.Corrosion`
- `Accessories` -> `04-Accessories`, `4.Accessories`
- `Door Adjustment and Operation` -> `05-Door`, `5.Door`
- `Floor Placement` -> `06-Floor`, `6.Floor`
- `Window Operation and Condition` -> `08-Window`, `8.Window`
- `Glass Condition` -> `09-Glass`, `9.Glass`
- `Inner Appearance and Fixation of Seats` -> `10-Inner`, `10.Inner`
- `Tire and Wheel Condition` -> `11-Tire`, `11.Tire`
- `Exterior Lights Condition` -> `12-Exterior`, `12.Exterior`
- `Painting Condition` -> `13-Painting`, `13.Painting`
- `Mirrors Condition` -> `14-Mirrors`, `14.Mirrors`

## Commands

Dry-run one task. This still downloads images and creates the local PDF, but it
does not upload the report or update ClickUp:

```bash
python3 -m inspection_reports.cli one --task-id 86dzb0pdd --dry-run
```

Run one task and write back the SharePoint report link:

```bash
python3 -m inspection_reports.cli one --task-id TASK_ID
```

Dry-run the first five tasks from the configured list:

```bash
python3 -m inspection_reports.cli batch --max-tasks 5 --dry-run
```

Run the first five tasks:

```bash
python3 -m inspection_reports.cli batch --max-tasks 5
```

## ClickUp Fields To Confirm

- The per-truck ClickUp list ID.
- The field that should receive the generated report URL.
- The field that uniquely identifies the truck in photo names/folders,
  preferably VIN or chassis number.
- Whether there is a task-specific SharePoint photos folder/link field.

## SharePoint Details To Confirm

- Hostname, for example `mtmlogixmx.sharepoint.com`.
- Site path, for example `/sites/Magna`.
- Source photos folder path.
- Output reports folder path.

### Security requirement for existing-report recovery

Set `INSPECTION_REPORT_OUTPUT_FOLDER_URL` or `INSPECTION_REPORT_OUTPUT_FOLDER_PATH` (with configured site/hostname) to the trusted location used for report uploads and recovery. Recovery validates the expected PDF's resolved drive/item identity before downloading a task-supplied report link. A configured global source folder is accepted as a fallback only for reports actually stored there. Task-specific photo folders are not authority for reading existing PDFs.

If older reports live in per-task folders and no explicit output location is configured, recovery now fails closed instead of copying their bytes. Review and migrate those reports into the configured report location before relying on recovery. New photo-based generation remains available. The change does not establish immutable task/VIN ownership or a general authorization policy for source images.
