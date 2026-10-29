from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.routes import Actor
from app.metadata.service import ConnectorUnavailableError
from app.operator.models import RunRecord
from app.reports.models import (
    CardQuestion,
    CardRefresh,
    CardRefreshRequest,
    FilterColumn,
    FilterOptions,
    ReportAccess,
    ReportCreate,
    ReportInput,
    ReportSummary,
    ReportView,
)
from app.reports.service import ReportForbiddenError, ReportRejectedError, ReportService

router = APIRouter(prefix="/api/reports")


def get_report_service() -> ReportService:
    """Composition hook for reports; tests can replace this dependency."""
    from app.main import report_service

    return report_service


Reports = Annotated[ReportService, Depends(get_report_service)]


def _call[T](call: Callable[[], T | None]) -> T:
    """Map the service's refusals to HTTP; a report the actor cannot open is missing."""
    try:
        result = call()
    except ReportRejectedError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ReportForbiddenError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    if result is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return result


@router.get("", response_model=list[ReportSummary])
def list_reports(service: Reports, actor: Actor) -> list[ReportSummary]:
    return service.list(actor)


@router.post("", response_model=ReportView, status_code=201)
def create_report(payload: ReportCreate, service: Reports, actor: Actor) -> ReportView:
    return _call(lambda: service.create(payload, actor))


@router.get("/{report_id}", response_model=ReportView)
def get_report(report_id: str, service: Reports, actor: Actor) -> ReportView:
    return _call(lambda: service.get(report_id, actor))


@router.put("/{report_id}", response_model=ReportView)
def update_report(
    report_id: str, payload: ReportInput, service: Reports, actor: Actor
) -> ReportView:
    return _call(lambda: service.update(report_id, payload, actor))


@router.put("/{report_id}/access", response_model=ReportView)
def share_report(
    report_id: str, payload: ReportAccess, service: Reports, actor: Actor
) -> ReportView:
    return _call(lambda: service.share(report_id, payload, actor))


@router.delete("/{report_id}", status_code=204)
def delete_report(report_id: str, service: Reports, actor: Actor) -> None:
    _call(lambda: service.delete(report_id, actor) or None)


async def _run[T](call: Awaitable[T | None]) -> T:
    try:
        record = await call
    except ReportRejectedError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ReportForbiddenError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
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
async def refresh_card(
    report_id: str,
    card_id: str,
    service: Reports,
    actor: Actor,
    payload: CardRefreshRequest | None = None,
) -> CardRefresh:
    choices = payload.filters if payload is not None else {}
    return await _run(service.refresh(report_id, card_id, actor, choices))


@router.get("/{report_id}/filter-columns", response_model=list[FilterColumn])
async def filter_columns(report_id: str, service: Reports, actor: Actor) -> list[FilterColumn]:
    return await _run(service.filter_columns(report_id, actor))


@router.post("/{report_id}/filters/{filter_id}/options", response_model=FilterOptions)
async def filter_options(
    report_id: str, filter_id: str, service: Reports, actor: Actor
) -> FilterOptions:
    """Read the values a reader can pick; the read is a gated, audited query."""
    return await _run(service.filter_options(report_id, filter_id, actor))


@router.post("/{report_id}/cards/{card_id}/accept", response_model=ReportView)
async def accept_card(report_id: str, card_id: str, service: Reports, actor: Actor) -> ReportView:
    """Take the tables as they are now as the card's new basis."""
    return await _run(service.accept(report_id, card_id, actor))
