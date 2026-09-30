from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from clickup_integration.client import ClickUpClient

from inspection_reports.canonical import report_summary_from_canonical_payload
from inspection_reports.clickup import (
    build_identifier_values,
    get_field_details_by_ids,
    get_field_details_by_names,
    get_field_value_by_ids,
    get_field_value_by_names,
    prepare_report_link_writeback,
    summarize_task_for_report,
)
from inspection_reports.config import InspectionReportSettings
from inspection_reports.matching import build_match_terms, caption_from_item_path, item_matches_terms
from inspection_reports.models import ReportImage, SharePointItem
from inspection_reports.report import build_inspection_report_pdf, build_report_model
from inspection_reports.sharepoint import SharePointGraphClient, is_supported_image
from inspection_reports.template_docx import (
    build_docx_from_template,
    convert_docx_to_pdf,
)


@dataclass(frozen=True)
class ReportRunResult:
    task_id: str
    task_name: str
    status: str
    local_pdf_path: str | None
    matched_image_count: int
    local_docx_path: str | None = None
    uploaded_url: str | None = None
    clickup_writeback: dict[str, Any] | None = None
    picture_folder_writeback: dict[str, Any] | None = None
    report_file_writeback: dict[str, Any] | None = None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReportCompletionResult:
    task_id: str
    task_name: str
    previous_status: str | None
    status: str
    target_status: str
    status_updated: bool
    uploaded_url: str | None = None
    local_pdf_path: str | None = None
    picture_folder_url: str | None = None
    report_file_attachment_status: str | None = None
    matched_image_count: int = 0
    notes: tuple[str, ...] = ()


class InspectionReportWorkflow:
    def __init__(
        self,
        *,
        settings: InspectionReportSettings,
        clickup_client: ClickUpClient,
        sharepoint_client: SharePointGraphClient,
    ) -> None:
        self.settings = settings
        self.clickup = clickup_client
        self.sharepoint = sharepoint_client

    def run_task(
        self,
        task_id: str,
        *,
        dry_run: bool = False,
        ignore_status_filter: bool = False,
        _summary: dict[str, Any] | None = None,
    ) -> ReportRunResult:
        if _summary is None:
            task = self.clickup.get_task(
                task_id,
                custom_task_ids=self.settings.custom_task_ids,
                team_id=self.settings.clickup_team_id,
                include_subtasks=False,
            )
            summary = summarize_task_for_report(
                task,
                report_field_names=self.settings.report_field_names,
            )
        else:
            summary = _summary
        self._add_comment_field(summary)
        if not ignore_status_filter and not self._is_allowed_status(summary.get("status")):
            return ReportRunResult(
                task_id=summary["task_id"],
                task_name=summary.get("name") or "",
                status="skipped_status",
                local_pdf_path=None,
                local_docx_path=None,
                matched_image_count=0,
                notes=(
                    "Task status is not eligible for inspection report generation: "
                    f"{summary.get('status') or 'unknown'}",
                ),
            )
        notes: list[str] = []
        source_folder, exact_task_folder = self._resolve_source_folder(summary)
        try:
            image_items = self._match_image_items(
                source_folder=source_folder,
                summary=summary,
                exact_task_folder=exact_task_folder,
            )
        except RuntimeError:
            fallback_source_folder = self.settings.require_source_folder_path()
            if not exact_task_folder or fallback_source_folder == source_folder:
                raise
            notes.append(
                "Task photo folder was not accessible; used configured SharePoint "
                "source folder and matched photos by VIN."
            )
            source_folder = fallback_source_folder
            exact_task_folder = False
            image_items = self._match_image_items(
                source_folder=source_folder,
                summary=summary,
                exact_task_folder=exact_task_folder,
            )

        if not image_items:
            notes.append(f"No images matched in SharePoint folder: {source_folder}")
        matched_count = len(image_items)
        image_items = self._limit_image_items_for_template(image_items)
        if len(image_items) < matched_count:
            notes.append(
                f"Matched {matched_count} images; selected {len(image_items)} for folder sections."
            )
        if len(image_items) > self.settings.max_images_per_report:
            notes.append(
                f"Matched {len(image_items)} images; using first "
                f"{self.settings.max_images_per_report}."
            )
            image_items = image_items[: self.settings.max_images_per_report]
        picture_folder_url = self._picture_folder_url_for_images(
            summary=summary,
            source_folder=source_folder,
            exact_task_folder=exact_task_folder,
            image_items=image_items,
        )

        local_images = self._download_images(image_items, task_id=summary["task_id"])
        local_pdf_path, local_docx_path = self._build_report_outputs(
            summary=summary,
            images=local_images,
            notes=tuple(notes),
        )

        if dry_run:
            dry_run_notes = notes + self._conversion_notes(local_pdf_path, local_docx_path)
            return ReportRunResult(
                task_id=summary["task_id"],
                task_name=summary.get("name") or "",
                status="dry_run",
                local_pdf_path=str(local_pdf_path) if local_pdf_path else None,
                local_docx_path=str(local_docx_path) if local_docx_path else None,
                matched_image_count=len(image_items),
                notes=tuple(dry_run_notes),
            )

        if not local_pdf_path:
            raise RuntimeError(
                "Template DOCX was generated, but PDF conversion is unavailable. "
                "Install LibreOffice or run on a machine with soffice before live upload/writeback."
            )

        upload_name = local_pdf_path.name
        output_folder = self._resolve_output_folder(source_folder)
        uploaded_item, report_url = self._upload_report(
            output_folder=output_folder,
            local_pdf_path=local_pdf_path,
            upload_name=upload_name,
        )
        writeback = prepare_report_link_writeback(
            summary,
            report_url=report_url,
            report_link_field_names=self.settings.report_link_field_names,
            report_link_field_ids=self.settings.report_link_field_ids,
        )
        picture_folder_writeback: dict[str, Any] | None = None
        report_file_writeback: dict[str, Any] | None = None
        if writeback.get("status") == "ready":
            self.clickup.set_task_custom_field_value(
                writeback["task_id"],
                writeback["field_id"],
                writeback["value"],
            )
            picture_folder_writeback = self._ensure_picture_folder_link(
                summary,
                image_items=image_items,
                source_folder=source_folder,
                exact_task_folder=exact_task_folder,
            )
            if picture_folder_writeback.get("status") == "updated":
                notes.append("Picture folder URL written back to ClickUp.")
            elif picture_folder_writeback.get("status") not in {"existing", "skipped_existing"}:
                notes.append(
                    "Picture folder URL was not written back: "
                    + str(picture_folder_writeback.get("status"))
                )
            report_file_writeback = self._ensure_report_file_attachment(
                summary,
                local_pdf_path=local_pdf_path,
            )
            if report_file_writeback.get("status") == "updated":
                notes.append("Report PDF attached to ClickUp Files field.")
            elif report_file_writeback.get("status") == "existing":
                notes.append("Report PDF was already attached to ClickUp Files field.")
            elif report_file_writeback.get("status") != "disabled":
                notes.append(
                    "Report PDF was not attached to ClickUp Files field: "
                    + str(report_file_writeback.get("status"))
                )
        else:
            notes.append(
                "Report uploaded, but ClickUp link writeback field was not found: "
                + ", ".join(writeback.get("missing_field_names") or ())
            )

        return ReportRunResult(
            task_id=summary["task_id"],
            task_name=summary.get("name") or "",
            status="uploaded",
            local_pdf_path=str(local_pdf_path),
            local_docx_path=str(local_docx_path) if local_docx_path else None,
            matched_image_count=len(image_items),
            uploaded_url=report_url or uploaded_item.web_url,
            clickup_writeback=writeback,
            picture_folder_writeback=picture_folder_writeback,
            report_file_writeback=report_file_writeback,
            notes=tuple(notes),
        )

    def run_canonical_payload(
        self,
        payload: dict[str, Any],
        *,
        task: dict[str, Any],
        dry_run: bool = False,
    ) -> ReportRunResult:
        summary = report_summary_from_canonical_payload(payload, task=task)
        return self.run_task(
            str(summary["task_id"]),
            dry_run=dry_run,
            ignore_status_filter=True,
            _summary=summary,
        )

    def complete_missing_reports_for_list(
        self,
        *,
        target_status: str,
        max_tasks: int | None = None,
        pages: int | None = None,
    ) -> list[ReportCompletionResult]:
        list_id = self.settings.require_clickup_list_id()
        results: list[ReportCompletionResult] = []
        processed = 0
        for task in self._iter_list_tasks(list_id=list_id, pages=pages):
            if max_tasks and processed >= max_tasks:
                break
            processed += 1
            results.append(self.complete_missing_report_for_task(task, target_status=target_status))
        return results

    def complete_missing_report_for_task(
        self,
        task: dict[str, Any],
        *,
        target_status: str,
        canonical_payload: dict[str, Any] | None = None,
    ) -> ReportCompletionResult:
        summary = summarize_task_for_report(
            task,
            report_field_names=self.settings.report_field_names,
        )
        self._add_comment_field(summary)
        previous_status = summary.get("status")
        notes: list[str] = []

        existing_link = self._existing_report_link(summary)
        if existing_link:
            picture_folder_writeback = self._ensure_picture_folder_link(summary)
            if not self._picture_folder_writeback_ok(picture_folder_writeback):
                return ReportCompletionResult(
                    task_id=summary["task_id"],
                    task_name=summary.get("name") or "",
                    previous_status=previous_status,
                    status="picture_folder_writeback_failed",
                    target_status=target_status,
                    status_updated=False,
                    uploaded_url=existing_link,
                    picture_folder_url=picture_folder_writeback.get("value"),
                    notes=(str(picture_folder_writeback),),
                )
            report_file_writeback = self._ensure_existing_report_file_attachment(
                summary,
                report_url=existing_link,
            )
            if not self._report_file_writeback_ok(report_file_writeback):
                return ReportCompletionResult(
                    task_id=summary["task_id"],
                    task_name=summary.get("name") or "",
                    previous_status=previous_status,
                    status="report_file_writeback_failed",
                    target_status=target_status,
                    status_updated=False,
                    uploaded_url=existing_link,
                    picture_folder_url=picture_folder_writeback.get("value"),
                    report_file_attachment_status=report_file_writeback.get("status"),
                    notes=(str(report_file_writeback),),
                )
            status_updated = self._update_task_status_if_needed(
                task_id=summary["task_id"],
                current_status=previous_status,
                target_status=target_status,
            )
            return ReportCompletionResult(
                task_id=summary["task_id"],
                task_name=summary.get("name") or "",
                previous_status=previous_status,
                status="existing_clickup_link",
                target_status=target_status,
                status_updated=status_updated,
                uploaded_url=existing_link,
                picture_folder_url=picture_folder_writeback.get("value"),
                report_file_attachment_status=report_file_writeback.get("status"),
                notes=tuple(notes),
            )

        sharepoint_url = self._find_existing_uploaded_report_url(summary)
        if sharepoint_url:
            writeback = prepare_report_link_writeback(
                summary,
                report_url=sharepoint_url,
                report_link_field_names=self.settings.report_link_field_names,
                report_link_field_ids=self.settings.report_link_field_ids,
            )
            if writeback.get("status") != "ready":
                return ReportCompletionResult(
                    task_id=summary["task_id"],
                    task_name=summary.get("name") or "",
                    previous_status=previous_status,
                    status="missing_clickup_report_field",
                    target_status=target_status,
                    status_updated=False,
                    uploaded_url=sharepoint_url,
                    notes=("Existing SharePoint report found, but ClickUp report field was not found.",),
                )
            self.clickup.set_task_custom_field_value(
                writeback["task_id"],
                writeback["field_id"],
                writeback["value"],
            )
            picture_folder_writeback = self._ensure_picture_folder_link(summary)
            if not self._picture_folder_writeback_ok(picture_folder_writeback):
                return ReportCompletionResult(
                    task_id=summary["task_id"],
                    task_name=summary.get("name") or "",
                    previous_status=previous_status,
                    status="picture_folder_writeback_failed",
                    target_status=target_status,
                    status_updated=False,
                    uploaded_url=sharepoint_url,
                    picture_folder_url=picture_folder_writeback.get("value"),
                    notes=(str(picture_folder_writeback),),
                )
            report_file_writeback = self._ensure_existing_report_file_attachment(
                summary,
                report_url=sharepoint_url,
            )
            if not self._report_file_writeback_ok(report_file_writeback):
                return ReportCompletionResult(
                    task_id=summary["task_id"],
                    task_name=summary.get("name") or "",
                    previous_status=previous_status,
                    status="report_file_writeback_failed",
                    target_status=target_status,
                    status_updated=False,
                    uploaded_url=sharepoint_url,
                    picture_folder_url=picture_folder_writeback.get("value"),
                    report_file_attachment_status=report_file_writeback.get("status"),
                    notes=(str(report_file_writeback),),
                )
            status_updated = self._update_task_status_if_needed(
                task_id=summary["task_id"],
                current_status=previous_status,
                target_status=target_status,
            )
            return ReportCompletionResult(
                task_id=summary["task_id"],
                task_name=summary.get("name") or "",
                previous_status=previous_status,
                status="linked_existing_sharepoint_report",
                target_status=target_status,
                status_updated=status_updated,
                uploaded_url=sharepoint_url,
                picture_folder_url=picture_folder_writeback.get("value"),
                report_file_attachment_status=report_file_writeback.get("status"),
                notes=tuple(notes),
            )

        run_task_id = summary.get("custom_id") or summary["task_id"]
        try:
            if canonical_payload:
                run_result = self.run_canonical_payload(
                    canonical_payload,
                    task=task,
                    dry_run=False,
                )
            else:
                run_result = self.run_task(
                    str(run_task_id),
                    dry_run=False,
                    ignore_status_filter=True,
                )
        except Exception as exc:  # noqa: BLE001 - keep batch moving and leave status unchanged.
            return ReportCompletionResult(
                task_id=summary["task_id"],
                task_name=summary.get("name") or "",
                previous_status=previous_status,
                status="failed",
                target_status=target_status,
                status_updated=False,
                notes=(str(exc),),
            )

        if run_result.status != "uploaded" or not run_result.uploaded_url:
            return ReportCompletionResult(
                task_id=summary["task_id"],
                task_name=summary.get("name") or "",
                previous_status=previous_status,
                status=run_result.status,
                target_status=target_status,
                status_updated=False,
                uploaded_url=run_result.uploaded_url,
                local_pdf_path=run_result.local_pdf_path,
                matched_image_count=run_result.matched_image_count,
                notes=run_result.notes,
            )

        if not run_result.clickup_writeback or run_result.clickup_writeback.get("status") != "ready":
            return ReportCompletionResult(
                task_id=summary["task_id"],
                task_name=summary.get("name") or "",
                previous_status=previous_status,
                status="uploaded_without_clickup_link",
                target_status=target_status,
                status_updated=False,
                uploaded_url=run_result.uploaded_url,
                local_pdf_path=run_result.local_pdf_path,
                picture_folder_url=(
                    run_result.picture_folder_writeback or {}
                ).get("value"),
                matched_image_count=run_result.matched_image_count,
                notes=run_result.notes,
            )

        picture_folder_writeback = run_result.picture_folder_writeback or {}
        if not self._picture_folder_writeback_ok(picture_folder_writeback):
            return ReportCompletionResult(
                task_id=summary["task_id"],
                task_name=summary.get("name") or "",
                previous_status=previous_status,
                status="picture_folder_writeback_failed",
                target_status=target_status,
                status_updated=False,
                uploaded_url=run_result.uploaded_url,
                local_pdf_path=run_result.local_pdf_path,
                picture_folder_url=picture_folder_writeback.get("value"),
                matched_image_count=run_result.matched_image_count,
                notes=run_result.notes + (str(picture_folder_writeback),),
            )

        report_file_writeback = run_result.report_file_writeback or {"status": "disabled"}
        if not self._report_file_writeback_ok(report_file_writeback):
            return ReportCompletionResult(
                task_id=summary["task_id"],
                task_name=summary.get("name") or "",
                previous_status=previous_status,
                status="report_file_writeback_failed",
                target_status=target_status,
                status_updated=False,
                uploaded_url=run_result.uploaded_url,
                local_pdf_path=run_result.local_pdf_path,
                picture_folder_url=picture_folder_writeback.get("value"),
                report_file_attachment_status=report_file_writeback.get("status"),
                matched_image_count=run_result.matched_image_count,
                notes=run_result.notes + (str(report_file_writeback),),
            )

        status_updated = self._update_task_status_if_needed(
            task_id=summary["task_id"],
            current_status=previous_status,
            target_status=target_status,
        )
        return ReportCompletionResult(
            task_id=summary["task_id"],
            task_name=summary.get("name") or "",
            previous_status=previous_status,
            status="generated",
            target_status=target_status,
            status_updated=status_updated,
            uploaded_url=run_result.uploaded_url,
            local_pdf_path=run_result.local_pdf_path,
            picture_folder_url=picture_folder_writeback.get("value"),
            report_file_attachment_status=report_file_writeback.get("status"),
            matched_image_count=run_result.matched_image_count,
            notes=run_result.notes,
        )

    def list_task_ids(self, *, max_tasks: int | None = None, pages: int = 1) -> list[str]:
        list_id = self.settings.require_clickup_list_id()
        task_ids: list[str] = []
        for task in self._iter_list_tasks(list_id=list_id, pages=pages):
            status = (task.get("status") or {}).get("status")
            if not self._is_allowed_status(status):
                continue
            if task.get("id"):
                task_ids.append(task["id"])
            if max_tasks and len(task_ids) >= max_tasks:
                return task_ids
        return task_ids

    def find_tasks_by_text(
        self,
        query: str,
        *,
        pages: int | None = None,
        include_closed: bool = False,
    ) -> list[dict[str, Any]]:
        list_id = self.settings.require_clickup_list_id()
        needle = query.strip().lower()
        matches: list[dict[str, Any]] = []
        for task in self._iter_list_tasks(
            list_id=list_id,
            pages=pages,
            include_closed=include_closed,
        ):
            summary = summarize_task_for_report(
                task,
                report_field_names=self.settings.report_field_names,
            )
            haystack_values = [
                summary.get("task_id"),
                summary.get("custom_id"),
                summary.get("name"),
                summary.get("url"),
                *(summary.get("report_fields") or {}).values(),
            ]
            haystack = " ".join(str(value) for value in haystack_values if value).lower()
            if needle and needle not in haystack:
                continue
            matches.append(
                {
                    "task_id": summary.get("task_id"),
                    "custom_id": summary.get("custom_id"),
                    "name": summary.get("name"),
                    "status": summary.get("status"),
                    "url": summary.get("url"),
                }
            )
        return matches

    def _iter_list_tasks(
        self,
        *,
        list_id: str,
        pages: int | None,
        include_closed: bool = False,
    ) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        page = 0
        while pages is None or page < pages:
            payload = self.clickup.get_list_tasks(
                list_id,
                archived=False,
                include_closed=include_closed,
                page=page,
            )
            page_tasks = payload.get("tasks") or []
            if not page_tasks:
                break
            tasks.extend(page_tasks)
            page += 1
        return tasks

    def _existing_report_link(self, summary: dict[str, Any]) -> str | None:
        custom_fields = summary.get("custom_fields") or {}
        link = get_field_value_by_ids(custom_fields, self.settings.report_link_field_ids)
        if not link:
            link = get_field_value_by_names(custom_fields, self.settings.report_link_field_names)
        return link

    def _existing_picture_folder_link(self, summary: dict[str, Any]) -> str | None:
        custom_fields = summary.get("custom_fields") or {}
        link = get_field_value_by_ids(custom_fields, self.settings.picture_folder_field_ids)
        if not link and not self.settings.picture_folder_field_ids:
            link = get_field_value_by_names(custom_fields, self.settings.picture_folder_field_names)
        return link

    def _ensure_picture_folder_link(
        self,
        summary: dict[str, Any],
        *,
        image_items: list[SharePointItem] | None = None,
        source_folder: str | None = None,
        exact_task_folder: bool = False,
    ) -> dict[str, Any]:
        existing = self._existing_picture_folder_link(summary)
        if existing:
            return self._write_picture_folder_link(
                summary,
                picture_folder_url=existing,
                overwrite_existing=True,
            )

        try:
            if image_items is None:
                source_folder, exact_task_folder, image_items = self._match_images_for_picture_folder(
                    summary
                )
            picture_folder_url = self._picture_folder_url_for_images(
                summary=summary,
                source_folder=source_folder or self.settings.require_source_folder_path(),
                exact_task_folder=exact_task_folder,
                image_items=image_items,
            )
        except Exception as exc:  # noqa: BLE001 - do not mark PASSED if this writeback cannot be resolved.
            return {
                "task_id": summary["task_id"],
                "status": "resolve_failed",
                "error": str(exc),
            }

        return self._write_picture_folder_link(summary, picture_folder_url=picture_folder_url)

    def _match_images_for_picture_folder(
        self,
        summary: dict[str, Any],
    ) -> tuple[str, bool, list[SharePointItem]]:
        source_folder, exact_task_folder = self._resolve_source_folder(summary)
        try:
            image_items = self._match_image_items(
                source_folder=source_folder,
                summary=summary,
                exact_task_folder=exact_task_folder,
            )
        except RuntimeError:
            fallback_source_folder = self.settings.require_source_folder_path()
            if not exact_task_folder or fallback_source_folder == source_folder:
                raise
            source_folder = fallback_source_folder
            exact_task_folder = False
            image_items = self._match_image_items(
                source_folder=source_folder,
                summary=summary,
                exact_task_folder=exact_task_folder,
            )
        return source_folder, exact_task_folder, image_items

    def _picture_folder_url_for_images(
        self,
        *,
        summary: dict[str, Any],
        source_folder: str,
        exact_task_folder: bool,
        image_items: list[SharePointItem],
    ) -> str | None:
        if exact_task_folder and _is_url(source_folder):
            return source_folder
        folder_item = self._picture_folder_item_for_images(summary, image_items)
        if not folder_item:
            return None
        return self.sharepoint.create_view_link_for_item(folder_item)

    def _picture_folder_item_for_images(
        self,
        summary: dict[str, Any],
        image_items: list[SharePointItem],
    ) -> SharePointItem | None:
        terms = build_match_terms(
            build_identifier_values(
                summary,
                field_names=self.settings.image_match_field_names,
            )
        )
        if not terms:
            return None

        for item in image_items:
            parts = [part for part in item.path.split("/") if part]
            for index, part in enumerate(parts[:-1]):
                normalized_part = _normalize_for_match(part)
                if any(term in normalized_part for term in terms):
                    folder_path = "/".join(parts[: index + 1])
                    return self.sharepoint.get_item_by_drive_path(
                        drive_id=item.drive_id,
                        item_path=folder_path,
                    )
        return None

    def _write_picture_folder_link(
        self,
        summary: dict[str, Any],
        *,
        picture_folder_url: str | None,
        overwrite_existing: bool = False,
    ) -> dict[str, Any]:
        if not picture_folder_url:
            return {
                "task_id": summary["task_id"],
                "status": "missing_url",
            }

        existing = self._existing_picture_folder_link(summary)
        if existing and not overwrite_existing:
            return {
                "task_id": summary["task_id"],
                "status": "existing",
                "value": existing,
            }

        custom_fields = summary.get("custom_fields") or {}
        field = get_field_details_by_ids(custom_fields, self.settings.picture_folder_field_ids)
        if not field:
            field = get_field_details_by_names(custom_fields, self.settings.picture_folder_field_names)
        field_id = field.get("id") if field else None
        if not field_id and self.settings.picture_folder_field_ids:
            field_id = self.settings.picture_folder_field_ids[0]
        if not field_id:
            return {
                "task_id": summary["task_id"],
                "status": "missing_field",
                "value": picture_folder_url,
            }

        self.clickup.set_task_custom_field_value(
            summary["task_id"],
            field_id,
            picture_folder_url,
        )
        return {
            "task_id": summary["task_id"],
            "status": "updated",
            "field_id": field_id,
            "value": picture_folder_url,
        }

    def _picture_folder_writeback_ok(self, writeback: dict[str, Any]) -> bool:
        return writeback.get("status") in {"existing", "updated"}

    def _ensure_report_file_attachment(
        self,
        summary: dict[str, Any],
        *,
        local_pdf_path: Path,
    ) -> dict[str, Any]:
        if not self.settings.report_attachment_field_ids and not self.settings.report_attachment_field_names:
            return {
                "task_id": summary["task_id"],
                "status": "disabled",
            }

        if not local_pdf_path.exists():
            return {
                "task_id": summary["task_id"],
                "status": "missing_local_file",
                "file_name": local_pdf_path.name,
            }

        workspace_id = self.settings.clickup_team_id or self.settings.clickup_workspace_id
        if not workspace_id:
            return {
                "task_id": summary["task_id"],
                "status": "missing_workspace_id",
                "file_name": local_pdf_path.name,
            }

        custom_fields = summary.get("custom_fields") or {}
        field = get_field_details_by_ids(custom_fields, self.settings.report_attachment_field_ids)
        if not field:
            field = get_field_details_by_names(
                custom_fields,
                self.settings.report_attachment_field_names,
            )
        field_id = field.get("id") if field else None
        if not field_id and self.settings.report_attachment_field_ids:
            field_id = self.settings.report_attachment_field_ids[0]
        if not field_id:
            return {
                "task_id": summary["task_id"],
                "status": "missing_field",
                "file_name": local_pdf_path.name,
            }

        existing_value = (field or {}).get("value")
        existing_items = existing_value if isinstance(existing_value, list) else []
        existing_ids = [
            str(item.get("id"))
            for item in existing_items
            if isinstance(item, dict) and item.get("id")
        ]
        if any(
            isinstance(item, dict) and (item.get("title") or item.get("name")) == local_pdf_path.name
            for item in existing_items
        ):
            return {
                "task_id": summary["task_id"],
                "status": "existing",
                "field_id": field_id,
                "file_name": local_pdf_path.name,
            }

        attachment = self.clickup.upload_custom_field_attachment(
            str(workspace_id),
            field_id,
            local_pdf_path,
            file_name=local_pdf_path.name,
            mime_type="application/pdf",
        )
        attachment_id = attachment.get("id")
        if not attachment_id:
            return {
                "task_id": summary["task_id"],
                "status": "upload_missing_attachment_id",
                "field_id": field_id,
                "file_name": local_pdf_path.name,
            }

        self.clickup.set_task_custom_field_value(
            summary["task_id"],
            field_id,
            [*existing_ids, attachment_id],
        )
        return {
            "task_id": summary["task_id"],
            "status": "updated",
            "field_id": field_id,
            "attachment_id": attachment_id,
            "file_name": local_pdf_path.name,
        }

    def _ensure_existing_report_file_attachment(
        self,
        summary: dict[str, Any],
        *,
        report_url: str,
    ) -> dict[str, Any]:
        """Attach an existing SharePoint report before completing a task."""
        if not self.settings.report_attachment_field_ids and not self.settings.report_attachment_field_names:
            return {
                "task_id": summary["task_id"],
                "status": "disabled",
            }

        file_name = f"{_report_file_base(summary)}.pdf"
        if self._has_existing_report_file_attachment(summary, file_name=file_name):
            return {
                "task_id": summary["task_id"],
                "status": "existing",
                "file_name": file_name,
            }

        local_pdf_path = self.settings.output_dir / file_name
        try:
            expected = self._trusted_existing_report(summary)
            item = self.sharepoint.get_item_from_share_url(report_url)
            if (expected is None or not item.id or not item.drive_id
                    or (item.drive_id, item.id) != (expected.drive_id, expected.id)
                    or item.is_folder or item.mime_type != "application/pdf"
                    or item.name.casefold() != file_name.casefold()):
                raise ValueError("Existing report is not the expected PDF in the configured report location.")
            self.sharepoint.download_item(item, local_pdf_path)
        except Exception as exc:  # noqa: BLE001 - keep PASSED guarded on recovery runs.
            return {
                "task_id": summary["task_id"],
                "status": "download_failed",
                "file_name": file_name,
                "error": str(exc),
            }
        return self._ensure_report_file_attachment(summary, local_pdf_path=local_pdf_path)

    def _has_existing_report_file_attachment(
        self,
        summary: dict[str, Any],
        *,
        file_name: str,
    ) -> bool:
        custom_fields = summary.get("custom_fields") or {}
        field = get_field_details_by_ids(custom_fields, self.settings.report_attachment_field_ids)
        if not field:
            field = get_field_details_by_names(
                custom_fields,
                self.settings.report_attachment_field_names,
            )
        existing_items = (field or {}).get("value")
        if not isinstance(existing_items, list):
            return False
        expected = file_name.casefold()
        return any(
            isinstance(item, dict)
            and str(item.get("title") or item.get("name") or "").casefold() == expected
            for item in existing_items
        )

    def _report_file_writeback_ok(self, writeback: dict[str, Any]) -> bool:
        return writeback.get("status") in {"disabled", "existing", "updated"}

    def _trusted_existing_report(self, summary: dict[str, Any]):
        upload_name = f"{_report_file_base(summary)}.pdf"
        output_folder = (self.settings.sharepoint_output_folder_url
                         or self.settings.sharepoint_output_folder_path
                         or self.settings.sharepoint_source_folder_url
                         or self.settings.sharepoint_source_folder_path)
        if not output_folder:
            raise ValueError("Existing report recovery requires a configured SharePoint report location.")
        if _is_url(output_folder):
            folder = self.sharepoint.get_item_from_share_url(output_folder)
            item = self.sharepoint.find_child_file_by_name_from_folder_item(
                folder=folder,
                file_name=upload_name,
            )
        else:
            hostname, site_path = self.settings.require_sharepoint_location()
            item = self.sharepoint.find_child_file_by_name(
                hostname=hostname,
                site_path=site_path,
                folder_path=output_folder,
                file_name=upload_name,
            )
        if item and (item.is_folder or item.mime_type != "application/pdf"
                     or item.name.casefold() != upload_name.casefold()
                     or not item.drive_id or not item.id):
            raise ValueError("Configured report item is not a valid PDF.")
        return item

    def _find_existing_uploaded_report_url(self, summary: dict[str, Any]) -> str | None:
        if not (self.settings.sharepoint_output_folder_url or self.settings.sharepoint_output_folder_path
                or self.settings.sharepoint_source_folder_url or self.settings.sharepoint_source_folder_path):
            return None
        item = self._trusted_existing_report(summary)
        return self.sharepoint.create_view_link_for_item(item) if item else None

    def _update_task_status_if_needed(
        self,
        *,
        task_id: str,
        current_status: str | None,
        target_status: str,
    ) -> bool:
        if _status_equals(current_status, target_status):
            return False
        self.clickup.update_task(task_id, status=target_status)
        return True

    def _is_allowed_status(self, status: str | None) -> bool:
        if not self.settings.allowed_statuses:
            return True
        normalized = " ".join((status or "").strip().lower().split())
        allowed = {
            " ".join(value.strip().lower().split())
            for value in self.settings.allowed_statuses
            if value.strip()
        }
        return normalized in allowed

    def _resolve_source_folder(self, summary: dict[str, Any]) -> tuple[str, bool]:
        custom_fields = summary.get("custom_fields") or {}
        task_folder = get_field_value_by_names(
            custom_fields,
            self.settings.sharepoint_folder_field_names,
        )
        if task_folder:
            return _folder_path_from_value(task_folder), True
        return self.settings.require_source_folder_path(), False

    def _resolve_output_folder(self, source_folder: str) -> str:
        if self.settings.sharepoint_output_folder_url:
            return self.settings.sharepoint_output_folder_url
        if self.settings.sharepoint_output_folder_path:
            return self.settings.sharepoint_output_folder_path
        return source_folder

    def _match_image_items(
        self,
        *,
        source_folder: str,
        summary: dict[str, Any],
        exact_task_folder: bool,
    ) -> list[SharePointItem]:
        if _is_url(source_folder):
            folder = self.sharepoint.get_item_from_share_url(source_folder)
            items = self.sharepoint.list_files_recursive_from_folder_item(
                folder,
                max_depth=self.settings.max_sharepoint_depth,
            )
        else:
            hostname, site_path = self.settings.require_sharepoint_location()
            items = self.sharepoint.list_files_recursive(
                hostname=hostname,
                site_path=site_path,
                folder_path=source_folder,
                max_depth=self.settings.max_sharepoint_depth,
            )
        images = [item for item in items if is_supported_image(item, self.settings.image_extensions)]
        if exact_task_folder:
            return self._sort_image_items_by_section(images)

        identifiers = build_identifier_values(
            summary,
            field_names=self.settings.image_match_field_names,
        )
        terms = build_match_terms(identifiers)
        return self._sort_image_items_by_section(
            [item for item in images if item_matches_terms(item, terms)]
        )

    def _upload_report(
        self,
        *,
        output_folder: str,
        local_pdf_path: Path,
        upload_name: str,
    ) -> tuple[SharePointItem, str]:
        if _is_url(output_folder):
            folder = self.sharepoint.get_item_from_share_url(output_folder)
            uploaded_item = self.sharepoint.upload_file_to_folder_item(
                folder=folder,
                local_path=local_pdf_path,
                file_name=upload_name,
            )
            report_url = self.sharepoint.create_view_link_for_item(uploaded_item)
            return uploaded_item, report_url

        hostname, site_path = self.settings.require_sharepoint_location()
        uploaded_item = self.sharepoint.upload_file(
            hostname=hostname,
            site_path=site_path,
            folder_path=output_folder,
            local_path=local_pdf_path,
            file_name=upload_name,
        )
        item_path = "/".join(part.strip("/") for part in (output_folder, upload_name) if part)
        report_url = self.sharepoint.create_view_link(
            hostname=hostname,
            site_path=site_path,
            item_path=item_path,
        )
        return uploaded_item, report_url

    def _download_images(self, items: list[SharePointItem], *, task_id: str) -> tuple[ReportImage, ...]:
        image_dir = Path(tempfile.gettempdir()) / "inspection_reports" / _safe_filename(task_id) / "images"
        if image_dir.exists():
            shutil.rmtree(image_dir)
        image_dir.mkdir(parents=True, exist_ok=True)

        images: list[ReportImage] = []
        for index, item in enumerate(items, start=1):
            local_name = f"{index:02d}-{_safe_filename(item.name)}"
            local_path = self.sharepoint.download_item(item, image_dir / local_name)
            images.append(
                ReportImage(
                    name=item.name,
                    local_path=local_path,
                    source_path=item.path,
                    web_url=item.web_url,
                    caption=caption_from_item_path(item.path),
                    section=self._report_section_for_path(item.path),
                )
            )
        return tuple(images)

    def _limit_image_items_for_template(self, items: list[SharePointItem]) -> list[SharePointItem]:
        if self.settings.template_max_images_per_section <= 0:
            return items

        selected: list[SharePointItem] = []
        used_by_folder: dict[str, int] = {}
        for item in items:
            folder = _parent_folder_path(item.path)
            used = used_by_folder.get(folder, 0)
            if used >= self.settings.template_max_images_per_section:
                continue
            selected.append(item)
            used_by_folder[folder] = used + 1
        return selected

    def _sort_image_items_by_section(self, items: list[SharePointItem]) -> list[SharePointItem]:
        section_order = {
            section: index
            for index, section in enumerate(self.settings.section_folder_prefixes.keys())
        }
        return sorted(
            items,
            key=lambda item: (
                section_order.get(self._section_for_path(item.path) or "", 999),
                _folder_sort_number(_parent_folder_name(item.path)),
                item.path.lower(),
            ),
        )

    def _section_for_path(self, path: str) -> str | None:
        normalized_parts = [_normalize_for_match(part) for part in path.split("/")]
        for section, patterns in self.settings.section_folder_prefixes.items():
            for pattern in patterns:
                normalized_pattern = _normalize_for_match(pattern)
                if any(part.startswith(normalized_pattern) for part in normalized_parts):
                    return section
        return None

    def _report_section_for_path(self, path: str) -> str:
        return self._section_for_path(path) or _clean_folder_label(_parent_folder_name(path))

    def _build_report_outputs(
        self,
        *,
        summary: dict[str, Any],
        images: tuple[ReportImage, ...],
        notes: tuple[str, ...],
    ) -> tuple[Path | None, Path | None]:
        file_base = _report_file_base(summary)
        report = build_report_model(
            task_id=summary["task_id"],
            task_name=summary.get("name") or summary["task_id"],
            custom_id=summary.get("custom_id"),
            clickup_url=summary.get("url"),
            fields=summary.get("report_fields") or {},
            images=images,
            notes=notes,
        )
        if self.settings.use_legacy_docx_template and self.settings.template_docx_path:
            local_docx_path = self.settings.docx_output_dir / f"{file_base}.docx"
            build_docx_from_template(
                report=report,
                template_path=self.settings.template_docx_path,
                output_path=local_docx_path,
            )
            converted_pdf = convert_docx_to_pdf(local_docx_path, self.settings.output_dir)
            if converted_pdf:
                return converted_pdf, local_docx_path

            return None, local_docx_path

        local_pdf_path = self.settings.output_dir / f"{file_base}.pdf"
        return build_inspection_report_pdf(report, local_pdf_path), None

    def _conversion_notes(self, local_pdf_path: Path | None, local_docx_path: Path | None) -> list[str]:
        if local_docx_path and not local_pdf_path:
            return [
                "Template DOCX generated. PDF conversion is unavailable because LibreOffice is not installed."
            ]
        return []

    def _add_comment_field(self, summary: dict[str, Any]) -> None:
        custom_fields = summary.get("custom_fields") or {}
        comment = get_field_value_by_ids(custom_fields, self.settings.comment_field_ids)
        if not comment:
            comment = get_field_value_by_names(custom_fields, self.settings.comment_field_names)
        if not comment:
            return

        report_fields = dict(summary.get("report_fields") or {})
        report_fields.setdefault("Inspection AI Exec Summary", comment)
        summary["report_fields"] = report_fields


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "-", value).strip(" .-")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:140] or "inspection-report"


def _report_file_base(summary: dict[str, Any]) -> str:
    fields = summary.get("report_fields") or {}
    vin = fields.get("VIN number") or fields.get("VIN Number") or summary.get("name")
    return _safe_filename(str(vin or summary.get("task_id") or "inspection-report"))


def _folder_path_from_value(value: str) -> str:
    if _is_url(value) and "/:f:/" in value:
        return value
    parsed = urlparse(value)
    raw_path = parsed.path if parsed.scheme and parsed.netloc else value.split("?", 1)[0]
    path = unquote(raw_path).strip().strip("/")
    for marker in ("Shared Documents/", "Documentos compartidos/"):
        if marker in path:
            return path.split(marker, 1)[1].strip("/")
    return path


def _parent_folder_path(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _parent_folder_name(path: str) -> str:
    parent = _parent_folder_path(path)
    return parent.rsplit("/", 1)[-1] if parent else ""


def _clean_folder_label(value: str) -> str:
    cleaned = re.sub(r"^\s*\d+\s*[-._)]\s*", "", value).strip()
    cleaned = cleaned.replace("_", " ").replace("-", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "Inspection Photos"


def _folder_sort_number(value: str) -> int:
    match = re.match(r"\s*(\d+)", value)
    return int(match.group(1)) if match else 999


def _is_url(value: str) -> bool:
    parsed = urlparse(value)
    return bool(parsed.scheme and parsed.netloc)


def _normalize_for_match(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _status_equals(left: str | None, right: str | None) -> bool:
    normalize = lambda value: " ".join((value or "").strip().lower().split())
    return normalize(left) == normalize(right)
