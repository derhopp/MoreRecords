"""Short-lived, authenticated and one-time downloadable CSV exports."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus
import logging
import re

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.auth import async_sign_path

from .const import DOMAIN
from .export_csv import (
    EXPORT_TICKET_SECONDS,
    ExportError,
    ExportTicketStore,
    ExportTooLarge,
    render_csv,
)
from .recorder_export import async_export_rows

_LOGGER = logging.getLogger(__name__)
_EXPORT_STORE = "export_tickets"


def get_export_store(hass) -> ExportTicketStore:
    """Obtain the store registered with the integration's HTTP endpoint."""
    return hass.data[DOMAIN][_EXPORT_STORE]


def signed_export_url(
    hass, entry_id: str, entity_id: str, start_ts: float, end_ts: float
) -> str:
    """Generate a ten-minute signed relative URL and single-use ticket."""
    ticket_id = get_export_store(hass).create(
        entry_id, entity_id, start_ts, end_ts
    )
    return async_sign_path(
        hass, f"/api/more_records/export/{ticket_id}",
        timedelta(seconds=EXPORT_TICKET_SECONDS),
    )


class MoreRecordsExportView(HomeAssistantView):
    """Deliver a CSV attachment only to an authorized, signed request."""

    url = "/api/more_records/export/{ticket}"
    name = "api:more_records:export"
    requires_auth = True

    def __init__(self, hass) -> None:
        """Keep a reference to the running Home Assistant instance."""
        self._hass = hass

    async def get(self, request: web.Request, ticket: str) -> web.Response:
        """Consume a signed download ticket and return a CSV attachment."""
        export_ticket = get_export_store(self._hass).consume(ticket)
        if export_ticket is None:
            return web.Response(
                status=HTTPStatus.GONE, text="Export link has expired or was used"
            )
        try:
            rows = await async_export_rows(
                self._hass, export_ticket.entity_id,
                export_ticket.start_ts, export_ticket.end_ts,
            )
            if not rows:
                return web.Response(
                    status=HTTPStatus.NOT_FOUND,
                    text="No Recorder states remain in this time range",
                )
            document = render_csv(rows)
        except ExportTooLarge as err:
            return web.Response(
                status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE, text=str(err)
            )
        except (ExportError, ValueError, OverflowError) as err:
            _LOGGER.warning("Recorder CSV export failed: %s", err)
            return web.Response(
                status=HTTPStatus.UNPROCESSABLE_ENTITY,
                text="Could not create the CSV file",
            )
        except Exception:
            _LOGGER.exception("Unexpected Recorder export error")
            return web.Response(
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
                text="Recorder CSV export failed",
            )

        filename_entity = re.sub(
            r"[^a-z0-9_.-]", "_", export_ticket.entity_id.lower()
        )[:120]
        filename = f"more_records_{filename_entity}.csv"
        return web.Response(
            text=document,
            content_type="text/csv",
            charset="utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )


def register_export_view(hass) -> None:
    """Register the export endpoint once per Home Assistant instance."""
    state = hass.data.setdefault(DOMAIN, {})
    if _EXPORT_STORE not in state:
        state[_EXPORT_STORE] = ExportTicketStore()
    if not state.get("export_view_registered"):
        hass.http.register_view(MoreRecordsExportView(hass))
        state["export_view_registered"] = True
