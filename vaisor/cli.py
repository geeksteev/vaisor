"""Command-line entry point for ``vaisor``.

A thin front door: loading, device access and measurements all live in
:class:`~vaisor.bench.Bench`, the drivers and :class:`~vaisor.measurement.Reading`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable

from vaisor import __version__
from vaisor.bench import Bench
from vaisor.config import BenchConfigError, SerialConnection, load_bench
from vaisor.device import DeviceInfo
from vaisor.drivers.base import DriverError
from vaisor.drivers.multimeter import Multimeter
from vaisor.measurement import Reading
from vaisor.transport import Transport, TransportError, make_transport

MAX_CONSECUTIVE_ERRORS = 5
DEFAULT_CONFIG = "bench.yaml"
DEFAULT_INTERVAL = 0.5


def _non_negative_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid number: {text!r}") from None
    if not value >= 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return value


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid integer: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vaisor",
        description="Give AI hands and eyes on a real electronics bench.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help="bench file (default: $VAISOR_CONFIG, otherwise ./bench.yaml)",
    )
    sub = parser.add_subparsers(dest="command")

    devices = sub.add_parser("devices", help="list configured devices")
    devices.add_argument("--json", action="store_true", help="print a JSON array")

    read = sub.add_parser("read", help="read a measurement from a multimeter")
    read.add_argument("device", help="device name from the bench file")
    read.add_argument("--jsonl", action="store_true", help="one JSON object per line")
    read.add_argument("--watch", action="store_true", help="read repeatedly")
    read.add_argument(
        "--interval",
        type=_non_negative_float,
        default=DEFAULT_INTERVAL,
        metavar="SECONDS",
        help=f"seconds between readings with --watch (default: {DEFAULT_INTERVAL})",
    )
    read.add_argument(
        "--count",
        type=_positive_int,
        default=None,
        metavar="N",
        help="stop after N readings with --watch",
    )
    return parser


def _err(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _format_table(rows: list[list[str]]) -> str:
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return "\n".join(
        "  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)).rstrip()
        for row in rows
    )


def _port(info: DeviceInfo) -> str:
    return getattr(info.connection, "port", "")


def _info_dict(info: DeviceInfo) -> dict[str, object]:
    return {
        "name": info.name,
        "class": info.device_class.value,
        "driver": info.driver,
        "connection": info.connection.model_dump(),
        "capabilities": list(info.capabilities),
    }


def _cmd_devices(bench: Bench, args: argparse.Namespace) -> int:
    infos = bench.devices()
    if args.json:
        print(json.dumps([_info_dict(i) for i in infos]))
        return 0
    if not infos:
        print("no devices configured")
        return 0
    rows = [["NAME", "CLASS", "DRIVER", "PORT", "CAPABILITIES"]]
    for i in infos:
        rows.append(
            [i.name, i.device_class.value, i.driver, _port(i), ", ".join(i.capabilities)]
        )
    print(_format_table(rows))
    return 0


def format_reading(reading: Reading) -> str:
    if reading.overrange or reading.value is None:
        shown = "OL"
    else:
        shown = f"{reading.value:g}"
        if reading.unit:
            shown += f" {reading.unit}"
    detail = ", ".join(p for p in (reading.function, reading.range) if p)
    return f"{reading.device}: {shown} ({detail})"


def _emit(reading: Reading, jsonl: bool) -> None:
    print(json.dumps(reading.to_dict()) if jsonl else format_reading(reading), flush=True)


def _cmd_read(bench: Bench, args: argparse.Namespace) -> int:
    name: str = args.device
    infos = {i.name: i for i in bench.devices()}
    if name not in infos:
        valid = ", ".join(sorted(infos)) or "none"
        _err(f"vaisor: unknown device '{name}' (devices: {valid})")
        return 2
    if "read_measurement" not in infos[name].capabilities:
        _err(f"vaisor: device '{name}' does not support read_measurement")
        return 1
    if not args.watch and (args.count is not None or args.interval != DEFAULT_INTERVAL):
        _err("vaisor: --interval and --count only apply with --watch")
        return 2

    driver = bench.device(name)
    if not isinstance(driver, Multimeter):
        _err(f"vaisor: device '{name}' does not support read_measurement")
        return 1
    if not args.watch:
        _emit(driver.read_measurement(), args.jsonl)
        return 0

    done = 0
    errors = 0
    try:
        while True:
            try:
                reading = driver.read_measurement()
            except (DriverError, TransportError) as exc:
                errors += 1
                _err(f"vaisor: {exc}")
                if errors >= MAX_CONSECUTIVE_ERRORS:
                    _err(f"vaisor: giving up after {errors} consecutive errors")
                    return 1
            else:
                errors = 0
                done += 1
                _emit(reading, args.jsonl)
                if args.count is not None and done >= args.count:
                    break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    return 0


def main(
    argv: list[str] | None = None,
    *,
    transport_factory: Callable[[SerialConnection], Transport] | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0

    config_path = args.config or os.environ.get("VAISOR_CONFIG") or DEFAULT_CONFIG
    bench: Bench | None = None
    try:
        try:
            config = load_bench(config_path)
            bench = Bench.from_config(
                config, transport_factory=transport_factory or make_transport
            )
        except BenchConfigError as exc:
            _err(str(exc))
            return 2
        except DriverError as exc:
            # Driver resolution failures are a problem with the bench file.
            _err(f"vaisor: {exc}")
            return 2
        try:
            if args.command == "devices":
                return _cmd_devices(bench, args)
            return _cmd_read(bench, args)
        except KeyboardInterrupt:
            return 0
        except (DriverError, TransportError) as exc:
            _err(f"vaisor: {exc}")
            return 1
    finally:
        if bench is not None:
            try:
                bench.close()
            except Exception as exc:
                _err(f"vaisor: {exc}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
