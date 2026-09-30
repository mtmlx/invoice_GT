from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree

from inspection_reports.clickup import (
    build_identifier_values,
    get_field_value_by_ids,
    prepare_report_link_writeback,
    resolve_field_value,
    summarize_task_for_report,
)
from inspection_reports.config import InspectionReportSettings
from inspection_reports.matching import build_match_terms, caption_from_item_path, item_matches_terms
from inspection_reports.models import SharePointItem
from inspection_reports.models import InspectionReport, ReportImage
from inspection_reports.report import _group_images_by_folder, _photo_slots, build_inspection_report_pdf
from inspection_reports.template_docx import NS, _block_text, _make_checkpoint_table_two_columns, _single_line
from inspection_reports.workflow import InspectionReportWorkflow, _folder_path_from_value, _report_file_base


def test_summarize_task_for_report_extracts_configured_fields() -> None:
    task = {
        "id": "task-1",
        "custom_id": "TRUCK-001",
        "name": "Hyundai truck inspection",
        "status": {"status": "in progress"},
        "custom_fields": [
            {"id": "vin-field", "name": "VIN", "type": "short_text", "value": "VIN123"},
            {
                "id": "pod-field",
                "name": "Port Of Discharge",
                "type": "drop_down",
                "value": "pod-1",
                "type_config": {
                    "options": [
                        {"id": "pod-1", "name": "Puerto Quetzal"},
                    ]
                },
            },
        ],
    }

    summary = summarize_task_for_report(task, report_field_names=("VIN", "Port Of Discharge"))

    assert summary["report_fields"] == {
        "VIN": "VIN123",
        "Port Of Discharge": "Puerto Quetzal",
    }


def test_build_identifier_values_uses_task_and_custom_fields() -> None:
    summary = {
        "custom_id": "MTLX-100",
        "name": "Hyundai HD65",
        "custom_fields": {
            "VIN": {"id": "vin-field", "type": "short_text", "value": "VIN-ABC-123"},
        },
    }

    values = build_identifier_values(summary, field_names=("VIN",))

    assert values == ("MTLX-100", "Hyundai HD65", "VIN-ABC-123")


def test_get_field_value_by_ids_extracts_configured_comment_field() -> None:
    custom_fields = {
        "Inspection AI Exec Summary": {
            "id": "9f7e2a56-a0ef-481a-b2d3-65dac825989b",
            "type": "text",
            "value": "Passed inspection with minor observation.",
        }
    }

    assert (
        get_field_value_by_ids(
            custom_fields,
            ("9f7e2a56-a0ef-481a-b2d3-65dac825989b",),
        )
        == "Passed inspection with minor observation."
    )


def test_prepare_report_link_writeback_finds_field_case_insensitively() -> None:
    payload = prepare_report_link_writeback(
        {
            "task_id": "task-1",
            "custom_fields": {
                "physical inspection report": {"id": "field-report", "type": "url"},
            },
        },
        report_url="https://example.com/report.pdf",
        report_link_field_names=("Physical Inspection Report",),
    )

    assert payload["status"] == "ready"
    assert payload["field_id"] == "field-report"
    assert payload["value"] == "https://example.com/report.pdf"


def test_prepare_report_link_writeback_reports_missing_field() -> None:
    payload = prepare_report_link_writeback(
        {"task_id": "task-1", "custom_fields": {}},
        report_url="https://example.com/report.pdf",
        report_link_field_names=("Inspection Report Link",),
    )

    assert payload["status"] == "missing_field"
    assert payload["missing_field_names"] == ("Inspection Report Link",)


def test_prepare_report_link_writeback_accepts_known_field_id() -> None:
    payload = prepare_report_link_writeback(
        {"task_id": "task-1", "custom_fields": {}},
        report_url="https://example.com/report.pdf",
        report_link_field_names=("Inspection Report Link",),
        report_link_field_ids=("field-report",),
    )

    assert payload["status"] == "ready"
    assert payload["field_id"] == "field-report"


def test_resolve_field_value_handles_clickup_dates() -> None:
    assert resolve_field_value({"type": "date", "value": "1772323200000"}) == "2026-03-01"


def test_item_matches_terms_uses_normalized_path_and_name() -> None:
    item = SharePointItem(
        id="item-1",
        name="VIN_ABC_123_front.jpg",
        drive_id="drive-1",
        path="Inspections/Hyundai/VIN_ABC_123_front.jpg",
        web_url=None,
        mime_type="image/jpeg",
    )

    assert item_matches_terms(item, build_match_terms(("VIN-ABC-123",))) is True
    assert item_matches_terms(item, build_match_terms(("OTHER-UNIT",))) is False


def test_caption_from_item_path_cleans_photo_file_name() -> None:
    assert caption_from_item_path("Inspections/VIN123/front-left_view.jpg") == "front left view"


def test_single_line_note_collapses_multiline_text() -> None:
    assert _single_line("Line one\nLine two\tline three") == "Line one Line two line three"


def test_checkpoint_table_is_reduced_to_two_columns() -> None:
    table = etree.fromstring(
        """
        <w:tbl xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:tblGrid>
            <w:gridCol w:w="3200"/>
            <w:gridCol w:w="1600"/>
            <w:gridCol w:w="4800"/>
          </w:tblGrid>
          <w:tr>
            <w:tc><w:p><w:r><w:t>Checkpoint</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>Result</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>Notes</w:t></w:r></w:p></w:tc>
          </w:tr>
          <w:tr>
            <w:tc><w:p><w:r><w:t>360° Overview</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>Pass</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>Comment</w:t></w:r></w:p></w:tc>
          </w:tr>
        </w:tbl>
        """
    )

    _make_checkpoint_table_two_columns(table)

    assert len(table.findall("w:tblGrid/w:gridCol", NS)) == 2
    for row in table.findall("w:tr", NS):
        cells = row.findall("w:tc", NS)
        assert len(cells) == 2
        assert _block_text(cells[-1]) != "Notes"


def test_report_file_base_uses_vin_only() -> None:
    summary = {
        "custom_id": "MTLXMGN-200",
        "name": "LGDVE41H7VA602494",
        "report_fields": {"VIN number": "LGDVE41H7VA602494"},
    }

    assert _report_file_base(summary) == "LGDVE41H7VA602494"


def test_folder_path_from_sharepoint_url() -> None:
    value = (
        "https://mtmlogixmx.sharepoint.com/sites/Magna/Shared%20Documents/"
        "Inspection%20Photos/VIN123?web=1"
    )

    assert _folder_path_from_value(value) == "Inspection Photos/VIN123"


def test_folder_path_keeps_sharepoint_folder_share_url() -> None:
    value = "https://mtmlogixmx.sharepoint.com/:f:/s/MTMLogixTopManagement/abc123?e=token"

    assert _folder_path_from_value(value) == value


def test_status_filter_defaults_to_passed() -> None:
    settings = InspectionReportSettings.from_env()
    workflow = InspectionReportWorkflow(
        settings=settings,
        clickup_client=object(),
        sharepoint_client=object(),
    )

    assert workflow._is_allowed_status("passed") is True
    assert workflow._is_allowed_status("PASSED") is True
    assert workflow._is_allowed_status("in progress") is False


def test_section_matching_supports_dotted_sharepoint_folder_names() -> None:
    settings = InspectionReportSettings.from_env()
    workflow = InspectionReportWorkflow(
        settings=settings,
        clickup_client=object(),
        sharepoint_client=object(),
    )

    assert (
        workflow._section_for_path(
            "Magna Inspections/April 2026/VIN/2.General360° Overview/front.jpg"
        )
        == "General / 360° Overview"
    )
    assert (
        workflow._section_for_path(
            "Magna Inspections/April 2026/VIN/5.Door Adjustment and operation/door.jpg"
        )
        == "Door Adjustment and Operation"
    )


def test_template_image_limit_uses_sharepoint_folder_not_placeholders() -> None:
    settings = replace(
        InspectionReportSettings.from_env(),
        template_docx_path=None,
        template_max_images_per_section=4,
    )
    workflow = InspectionReportWorkflow(
        settings=settings,
        clickup_client=object(),
        sharepoint_client=object(),
    )
    items = [
        SharePointItem(
            id=f"a-{index}",
            name=f"a-{index}.jpg",
            drive_id="drive",
            path=f"VIN/2.General360° Overview/a-{index}.jpg",
            web_url=None,
            mime_type="image/jpeg",
        )
        for index in range(6)
    ] + [
        SharePointItem(
            id=f"b-{index}",
            name=f"b-{index}.jpg",
            drive_id="drive",
            path=f"VIN/13.Painting Condition/b-{index}.jpg",
            web_url=None,
            mime_type="image/jpeg",
        )
        for index in range(3)
    ]

    selected = workflow._limit_image_items_for_template(items)

    assert [item.id for item in selected] == ["a-0", "a-1", "a-2", "a-3", "b-0", "b-1", "b-2"]


def test_branded_report_groups_photos_by_source_folder_and_has_no_empty_slots(tmp_path: Path) -> None:
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"image")
    images = (
        ReportImage(
            name="front.jpg",
            local_path=image_path,
            source_path="VIN/02-General360/front.jpg",
            section="General / 360 Overview",
        ),
        ReportImage(
            name="rear.jpg",
            local_path=image_path,
            source_path="VIN/02-General360/rear.jpg",
            section="General / 360 Overview",
        ),
        ReportImage(
            name="door.jpg",
            local_path=image_path,
            source_path="VIN/05-Door/door.jpg",
            section="Door Adjustment and Operation",
        ),
    )

    grouped = _group_images_by_folder(images)

    assert [label for label, _ in grouped] == [
        "General / 360 Overview",
        "Door Adjustment and Operation",
    ]
    assert [len(group) for _, group in grouped] == [2, 1]
    assert len(_photo_slots(1)) == 1
    assert len(_photo_slots(2)) == 2
    assert len(_photo_slots(3)) == 3
    assert len(_photo_slots(4)) == 4


def test_branded_pdf_is_named_inspection_report_and_uses_portrait_pages(tmp_path: Path) -> None:
    from PIL import Image

    image_path = tmp_path / "vehicle.jpg"
    Image.new("RGB", (900, 675), "white").save(image_path)
    report = InspectionReport(
        task_id="task-1",
        task_name="VIN123",
        custom_id="MTLXMGN-1",
        clickup_url=None,
        generated_at=datetime.now(timezone.utc),
        fields={
            "VIN number": "VIN123",
            "Brand": "Dongfeng",
            "Model": "DF-350",
            "Destination Country": "Guatemala",
            "Inspection Date": "2026-07-03",
            "360 Overview Result": "Pass",
        },
        images=(
            ReportImage(
                name="vehicle.jpg",
                local_path=image_path,
                source_path="VIN/02-General360/vehicle.jpg",
                section="General / 360 Overview",
            ),
        ),
    )
    output = build_inspection_report_pdf(report, tmp_path / "VIN123.pdf")

    payload = output.read_bytes()

    assert output.exists()
    assert payload.startswith(b"%PDF-")
    assert b"Inspection Report" in payload
    assert b"Physical Inspection Report" not in payload


def test_workflow_uses_branded_pdf_by_default_even_when_legacy_template_is_configured(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = replace(
        InspectionReportSettings.from_env(),
        output_dir=tmp_path,
        template_docx_path=Path("legacy-template.docx"),
        use_legacy_docx_template=False,
    )
    workflow = InspectionReportWorkflow(
        settings=settings,
        clickup_client=object(),
        sharepoint_client=object(),
    )
    output = tmp_path / "VIN123.pdf"

    def fake_build_report(_report, output_path: Path) -> Path:
        output_path.write_bytes(b"%PDF-1.4\n")
        return output_path

    monkeypatch.setattr("inspection_reports.workflow.build_inspection_report_pdf", fake_build_report)

    local_pdf, local_docx = workflow._build_report_outputs(
        summary={
            "task_id": "task-1",
            "name": "VIN123",
            "custom_id": "MTLXMGN-1",
            "report_fields": {"VIN number": "VIN123"},
        },
        images=(),
        notes=(),
    )

    assert local_pdf == output
    assert local_docx is None


def test_report_file_attachment_uploads_and_sets_clickup_file_field(tmp_path: Path) -> None:
    local_pdf = tmp_path / "VIN123.pdf"
    local_pdf.write_bytes(b"%PDF-1.4\n")
    settings = replace(
        InspectionReportSettings.from_env(),
        clickup_team_id="8451352",
        report_attachment_field_ids=("file-field",),
        report_attachment_field_names=("Origin Inspection Report",),
    )
    clickup = _FakeClickUpForAttachments()
    workflow = InspectionReportWorkflow(
        settings=settings,
        clickup_client=clickup,
        sharepoint_client=object(),
    )

    result = workflow._ensure_report_file_attachment(
        {
            "task_id": "task-1",
            "custom_fields": {
                "Origin Inspection Report": {
                    "id": "file-field",
                    "value": [{"id": "existing.pdf", "title": "Existing.pdf"}],
                },
            },
        },
        local_pdf_path=local_pdf,
    )

    assert result["status"] == "updated"
    assert clickup.uploads == [
        {
            "workspace_id": "8451352",
            "field_id": "file-field",
            "file_name": "VIN123.pdf",
            "mime_type": "application/pdf",
        }
    ]
    assert clickup.field_updates == [
        {
            "task_id": "task-1",
            "field_id": "file-field",
            "value": ["existing.pdf", "uploaded.pdf"],
        }
    ]


def test_report_file_attachment_skips_existing_file(tmp_path: Path) -> None:
    local_pdf = tmp_path / "VIN123.pdf"
    local_pdf.write_bytes(b"%PDF-1.4\n")
    settings = replace(
        InspectionReportSettings.from_env(),
        clickup_team_id="8451352",
        report_attachment_field_ids=("file-field",),
        report_attachment_field_names=("Origin Inspection Report",),
    )
    clickup = _FakeClickUpForAttachments()
    workflow = InspectionReportWorkflow(
        settings=settings,
        clickup_client=clickup,
        sharepoint_client=object(),
    )

    result = workflow._ensure_report_file_attachment(
        {
            "task_id": "task-1",
            "custom_fields": {
                "Origin Inspection Report": {
                    "id": "file-field",
                    "value": [{"id": "uploaded.pdf", "title": "VIN123.pdf"}],
                },
            },
        },
        local_pdf_path=local_pdf,
    )

    assert result["status"] == "existing"
    assert clickup.uploads == []
    assert clickup.field_updates == []


def test_completion_attaches_existing_sharepoint_report_before_setting_passed(tmp_path: Path) -> None:
    settings = replace(
        InspectionReportSettings.from_env(),
        clickup_team_id="8451352",
        output_dir=tmp_path,
        sharepoint_output_folder_url="https://example.com/trusted-reports",
        report_link_field_ids=("report-field",),
        picture_folder_field_ids=("pictures-field",),
        report_attachment_field_ids=("attachment-field",),
    )
    clickup = _FakeClickUpForCompletion()
    workflow = InspectionReportWorkflow(
        settings=settings,
        clickup_client=clickup,
        sharepoint_client=_FakeSharePointForCompletion(),
    )
    task = {
        "id": "task-1",
        "name": "VIN123",
        "status": {"status": "Ready for Report"},
        "custom_fields": [
            {"id": "vin-field", "name": "VIN number", "type": "short_text", "value": "VIN123"},
            {
                "id": "report-field",
                "name": "Inspection Final Report URL",
                "type": "url",
                "value": "https://example.com/VIN123.pdf",
            },
            {
                "id": "pictures-field",
                "name": "OneDrive Pictures",
                "type": "url",
                        "value": "https://example.com/:f:/s/MTM/VIN123",
            },
            {"id": "attachment-field", "name": "Origin Inspection Report", "type": "attachment", "value": []},
        ],
    }

    result = workflow.complete_missing_report_for_task(task, target_status="PASSED")

    assert result.status == "existing_clickup_link"
    assert result.status_updated is True
    assert result.report_file_attachment_status == "updated"
    assert clickup.uploads[0]["file_name"] == "VIN123.pdf"
    assert clickup.status_updates == [{"task_id": "task-1", "status": "PASSED"}]


def test_existing_report_lookup_does_not_trust_task_folder_without_global_source(tmp_path: Path) -> None:
    settings = replace(
        InspectionReportSettings.from_env(),
        sharepoint_source_folder_url=None,
        sharepoint_source_folder_path=None,
        sharepoint_output_folder_url=None,
        sharepoint_output_folder_path=None,
        sharepoint_folder_field_names=("OneDrive Pictures",),
    )
    sharepoint = _FakeSharePointForExistingLookup()
    workflow = InspectionReportWorkflow(
        settings=settings,
        clickup_client=object(),
        sharepoint_client=sharepoint,
    )

    result = workflow._find_existing_uploaded_report_url(
        {
            "task_id": "task-1",
            "name": "VIN123",
            "custom_fields": {
                "OneDrive Pictures": {
                    "id": "pictures-field",
                    "type": "url",
                    "value": "https://example.com/:f:/s/MTM/VIN123",
                }
            },
            "report_fields": {"VIN number": "VIN123"},
        }
    )

    assert result is None
    assert sharepoint.looked_up_folder is None


class _FakeClickUpForAttachments:
    def __init__(self) -> None:
        self.uploads: list[dict] = []
        self.field_updates: list[dict] = []

    def upload_custom_field_attachment(
        self,
        workspace_id: str,
        field_id: str,
        local_path: Path,
        *,
        file_name: str | None = None,
        mime_type: str | None = None,
    ) -> dict:
        self.uploads.append(
            {
                "workspace_id": workspace_id,
                "field_id": field_id,
                "file_name": file_name or local_path.name,
                "mime_type": mime_type,
            }
        )
        return {"id": "uploaded.pdf", "title": file_name}

    def set_task_custom_field_value(self, task_id: str, field_id: str, value: object) -> dict:
        self.field_updates.append(
            {
                "task_id": task_id,
                "field_id": field_id,
                "value": value,
            }
        )
        return {}


class _FakeClickUpForCompletion(_FakeClickUpForAttachments):
    def __init__(self) -> None:
        super().__init__()
        self.status_updates: list[dict] = []

    def update_task(self, task_id: str, *, status: str | None = None, **_kwargs: object) -> dict:
        self.status_updates.append({"task_id": task_id, "status": status})
        return {}


class _FakeSharePointForCompletion:
    def get_item_from_share_url(self, _share_url: str) -> object:
        return SharePointItem(id="report", drive_id="trusted", name="VIN123.pdf", path="/reports/VIN123.pdf", web_url=None, mime_type="application/pdf")

    def find_child_file_by_name_from_folder_item(self, *, folder, file_name):
        return self.get_item_from_share_url("expected")

    def download_item(self, _item: object, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"%PDF-1.4\n")
        return destination


class _FakeSharePointForExistingLookup:
    def __init__(self) -> None:
        self.looked_up_folder: str | None = None

    def get_item_from_share_url(self, share_url: str) -> object:
        self.looked_up_folder = share_url
        return object()

    def find_child_file_by_name_from_folder_item(self, *, folder: object, file_name: str) -> object:
        assert folder is not None
        assert file_name == "VIN123.pdf"
        return object()

    def create_view_link_for_item(self, _item: object) -> str:
        return "https://example.com/VIN123.pdf"
