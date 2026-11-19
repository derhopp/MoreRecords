"""MoreRecords: one-shot historical CSV prepend for existing entities."""


async def async_setup_entry(hass, entry) -> bool:
    """Enable the import tool; no entities or background polling are created."""
    return True


async def async_unload_entry(hass, entry) -> bool:
    """Unload the import tool."""
    return True
