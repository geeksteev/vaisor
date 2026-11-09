"""The Bench runtime: configured devices, created lazily."""

from __future__ import annotations

from collections.abc import Callable

from vaisor.config import BenchConfig, SerialConnection
from vaisor.device import DeviceInfo
from vaisor.drivers.base import Driver, DriverError
from vaisor.registry import discover_drivers, get_driver
from vaisor.transport import Transport, make_transport


class Bench:
    def __init__(
        self,
        config: BenchConfig,
        driver_classes: dict[str, type[Driver]],
        transport_factory: Callable[[SerialConnection], Transport],
    ) -> None:
        self.config = config
        self._classes = driver_classes
        self._transport_factory = transport_factory
        self._open: dict[str, Driver] = {}

    @classmethod
    def from_config(
        cls,
        config: BenchConfig,
        drivers: dict[str, type[Driver]] | None = None,
        transport_factory: Callable[[SerialConnection], Transport] = make_transport,
    ) -> Bench:
        """Resolve every device's driver; no transport is created or opened."""
        if drivers is None:
            drivers = discover_drivers()
        classes: dict[str, type[Driver]] = {}
        for name, dev in config.devices.items():
            try:
                klass = get_driver(dev.driver, drivers)
            except DriverError as exc:
                raise DriverError(f"device '{name}': {exc}") from None
            if klass.device_class != dev.device_class:
                raise DriverError(
                    f"device '{name}': driver '{dev.driver}' is a "
                    f"{klass.device_class.value}, config says {dev.device_class.value}"
                )
            classes[name] = klass
        return cls(config, classes, transport_factory)

    def devices(self) -> list[DeviceInfo]:
        return [
            DeviceInfo(
                name=name,
                device_class=dev.device_class,
                driver=dev.driver,
                connection=dev.connection,
                capabilities=tuple(sorted(self._classes[name].capabilities)),
            )
            for name, dev in self.config.devices.items()
        ]

    def device(self, name: str) -> Driver:
        """Return the driver for ``name``, creating and opening it on first use."""
        if name not in self.config.devices:
            valid = ", ".join(sorted(self.config.devices)) or "none"
            raise DriverError(f"unknown device '{name}' (devices: {valid})")
        drv = self._open.get(name)
        if drv is None:
            dev = self.config.devices[name]
            transport = self._transport_factory(dev.connection)
            drv = self._classes[name](name, dev, transport)
            try:
                drv.open()
            except Exception as exc:
                # Release the port so a later retry is not blocked.
                try:
                    transport.close()
                except Exception:
                    pass
                if isinstance(exc, DriverError):
                    raise
                raise DriverError(f"device '{name}': failed to open: {exc}") from exc
            self._open[name] = drv
        return drv

    def close(self) -> None:
        """Close every device that was opened; keeps going if one fails."""
        errors: list[str] = []
        while self._open:
            name, drv = self._open.popitem()
            try:
                drv.close()
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        if errors:
            raise DriverError("error closing devices: " + "; ".join(errors))

    def __enter__(self) -> Bench:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
