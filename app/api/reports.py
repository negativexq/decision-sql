from collections.abc import Awaitable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.routes import Actor
from app.metadata.service import ConnectorUnavailableError
from app.operator.models import RunRecord
from app.reports.models import (
    CardQuestion,
    CardRefresh,
    Report,
    ReportCreate,
    ReportInput,
    ReportSummary,
)
from app.reports.service import ReportRejectedError, ReportService

router = APIRouter(prefix="/api/reports")


def get_report_service() -> ReportService:
    """Composition hook for reports; tests can replace this dependency."""
    from app.main import report_service

    return report_service


Reports = Annotated[ReportService, Depends(get_report_service)]


def _found(report: Report | None) -> Report:
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.get("", response_model=list[ReportSummary])
def list_reports(service: Reports) -> list[ReportSummary]:
    return service.list()


@router.post("", response_model=Report, status_code=201)
def create_report(payload: ReportCreate, service: Reports, actor: Actor) -> Report:
    try:
        return service.create(payload, actor)
    except ReportRejectedError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/{report_id}", response_model=Report)
def get_report(report_id: str, service: Reports) -> Report:
    return _found(service.get(report_id))


@router.put("/{report_id}", response_model=Report)
def update_report(report_id: str, payload: ReportInput, service: Reports, actor: Actor) -> Report:
    try:
        return _found(service.update(report_id, payload, actor))
    except ReportRejectedError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.delete("/{report_id}", status_code=204)
def delete_report(report_id: str, service: Reports) -> None:
    if not service.delete(report_id):
        raise HTTPException(status_code=404, detail="Report not found")


async def _run[T](call: Awaitable[T | None]) -> T:
    try:
        record = await call
    except ReportRejectedError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ConnectorUnavailableError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception as error:  # driver errors are reported, not leaked as 500s
        raise HTTPException(
            status_code=502, detail="Could not read the schema of this connection."
        ) from error
    if record is None:
        raise HTTPException(status_code=404, detail="Report or card not found")
    return record


@router.post("/{report_id}/ask", response_model=RunRecord)
async def ask_for_card(
    report_id: str, payload: CardQuestion, service: Reports, actor: Actor
) -> RunRecord:
    return await _run(service.ask(report_id, payload.question, actor))


@router.post("/{report_id}/cards/{card_id}/refresh", response_model=CardRefresh)
async def refresh_card(report_id: str, card_id: str, service: Reports, actor: Actor) -> CardRefresh:
    return await _run(service.refresh(report_id, card_id, actor))


@router.post("/{report_id}/cards/{card_id}/accept", response_model=Report)
async def accept_card(report_id: str, card_id: str, service: Reports, actor: Actor) -> Report:
    """Take the tables as they are now as the card's new basis."""
    return await _run(service.accept(report_id, card_id, actor))
