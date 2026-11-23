"""MoreRecords: one-time Recorder history import and CSV export."""


async def async_setup_entry(hass, entry) -> bool:
    """Register the authenticated CSV export endpoint."""
    from .export_view import register_export_view
    from .retention_anchors import register_retention_tracking

    register_export_view(hass)
    register_retention_tracking(hass, entry)
    return True


async def async_unload_entry(hass, entry) -> bool:
    """Revoke outstanding export URLs when the integration unloads."""
    from .export_view import get_export_store

    get_export_store(hass).clear_entry(entry.entry_id)
    entry.async_unload()
    return True
