"""Driver discovery through the ``vaisor.drivers`` entry-point group."""

from __future__ import annotations

import importlib.metadata
import inspect

from vaisor.drivers.base import Driver, DriverError

ENTRY_POINT_GROUP = "vaisor.drivers"


def discover_drivers(
    warnings: list[str] | None = None,
) -> dict[str, type[Driver]]:
    """Return installed drivers by entry-point name.

    Entry points that fail to import or are not :class:`Driver` subclasses are
    skipped and described in ``warnings`` (if given). Two entry points with the
    same name raise :class:`DriverError`.
    """
    found: dict[str, type[Driver]] = {}
    sources: dict[str, str] = {}
    for ep in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP):
        try:
            obj = ep.load()
        except Exception as exc:  # third-party code: never crash discovery
            _warn(warnings, f"driver '{ep.name}' ({ep.value}) failed to import: {exc!r}")
            continue
        if not (inspect.isclass(obj) and issubclass(obj, Driver)):
            _warn(warnings, f"driver '{ep.name}' ({ep.value}) is not a Driver subclass")
            continue
        if ep.name in found:
            raise DriverError(
                f"duplicate driver name '{ep.name}': {sources[ep.name]} and {ep.value}"
            )
        found[ep.name] = obj
        sources[ep.name] = ep.value
    return found


def _warn(warnings: list[str] | None, message: str) -> None:
    if warnings is not None:
        warnings.append(message)


def get_driver(
    name: str, drivers: dict[str, type[Driver]] | None = None
) -> type[Driver]:
    """Look up a driver class by name, listing installed names on failure."""
    if drivers is None:
        drivers = discover_drivers()
    try:
        return drivers[name]
    except KeyError:
        installed = ", ".join(sorted(drivers)) or "none"
        raise DriverError(f"unknown driver '{name}' (installed: {installed})") from None
