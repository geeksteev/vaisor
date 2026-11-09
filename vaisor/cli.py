"""Command-line entry point for ``vaisor``.

A thin front door: all logic lives in ``Bench``, drivers and ``Reading``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable

from vaisor import __version__
from vaisor.audit import AuditError
from vaisor.bench import Bench
from vaisor.config import BenchConfigError, SerialConnection, load_bench
from vaisor.device import DeviceInfo
from vaisor.drivers.base import DriverError
from vaisor.measurement import Reading
from vaisor.safety import SafetyRefused
from vaisor.transport import Transport, TransportError

MAX_CONSECUTIVE_ERRORS = 5
READ_CAPABILITY = "read_measurement"

TransportFactory = Callable[[SerialConnection], Transport]


class UsageError(Exception):
    """Bad user input (exit code 2)."""


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
        help="bench file (default: $VAISOR_CONFIG, else ./bench.yaml)",
    )
    sub = parser.add_subparsers(dest="command")

    p_dev = sub.add_parser("devices", help="list configured devices")
    p_dev.add_argument("--json", action="store_true", help="print a JSON array")

    p_read = sub.add_parser("read", help="read a measurement from a multimeter")
    p_read.add_argument("device", help="device name from the bench file")
    p_read.add_argument(
        "--caller", default="cli", help="caller name recorded in the audit log (default cli)"
    )
    p_read.add_argument("--jsonl", action="store_true", help="one JSON object per line")
    p_read.add_argument("--watch", action="store_true", help="read repeatedly")
    p_read.add_argument(
        "--interval",
        type=_non_negative_float,
        default=0.5,
        metavar="SECONDS",
        help="seconds between readings with --watch (default 0.5)",
    )
    p_read.add_argument(
        "--count",
        type=_positive_int,
        default=None,
        metavar="N",
        help="stop after N readings with --watch",
    )
    return parser


def _info_dict(info: DeviceInfo) -> dict[str, object]:
    return {
        "name": info.name,
        "class": info.device_class.value,
        "driver": info.driver,
        "port": info.connection.port,
        "capabilities": list(info.capabilities),
    }


def _print_devices(infos: list[DeviceInfo], as_json: bool) -> None:
    rows = [_info_dict(i) for i in infos]
    if as_json:
        print(json.dumps(rows))
        return
    header = ["NAME", "CLASS", "DRIVER", "PORT", "CAPABILITIES"]
    table = [header] + [
        [
            str(r["name"]),
            str(r["class"]),
            str(r["driver"]),
            str(r["port"]),
            ", ".join(r["capabilities"]),  # type: ignore[arg-type]
        ]
        for r in rows
    ]
    widths = [max(len(row[i]) for row in table) for i in range(len(header))]
    for row in table:
        print("  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip())


def format_reading(reading: Reading) -> str:
    if reading.overrange or reading.value is None:
        shown = "OL"
    else:
        shown = f"{reading.value:g}"
    if reading.unit:
        shown += f" {reading.unit}"
    detail = ", ".join(x for x in (reading.function, reading.range) if x)
    return f"{reading.device}: {shown} ({detail})"


def _emit(reading: Reading, jsonl: bool) -> None:
    print(json.dumps(reading.to_dict()) if jsonl else format_reading(reading), flush=True)


def _cmd_devices(bench: Bench, args: argparse.Namespace) -> int:
    _print_devices(bench.devices(), args.json)
    return 0


def _cmd_read(bench: Bench, args: argparse.Namespace) -> int:
    infos = {i.name: i for i in bench.devices()}
    if args.device not in infos:
        valid = ", ".join(sorted(infos)) or "none"
        raise UsageError(f"unknown device '{args.device}' (devices: {valid})")
    if READ_CAPABILITY not in infos[args.device].capabilities:
        raise UsageError(
            f"device '{args.device}' does not support {READ_CAPABILITY} "
            f"(capabilities: {', '.join(infos[args.device].capabilities) or 'none'})"
        )

    if not args.watch:
        _emit(bench.run(args.caller, args.device, READ_CAPABILITY), args.jsonl)
        return 0

    done = 0
    consecutive = 0
    try:
        while args.count is None or done < args.count:
            if done:
                time.sleep(args.interval)
            done += 1
            try:
                reading = bench.run(args.caller, args.device, READ_CAPABILITY)
            except (DriverError, TransportError) as exc:
                consecutive += 1
                print(f"vaisor: error: {exc}", file=sys.stderr, flush=True)
                if consecutive >= MAX_CONSECUTIVE_ERRORS:
                    print(
                        f"vaisor: error: giving up after {consecutive} consecutive errors",
                        file=sys.stderr,
                    )
                    return 1
                continue
            consecutive = 0
            _emit(reading, args.jsonl)
    except KeyboardInterrupt:
        return 0
    return 0


def main(
    argv: list[str] | None = None,
    *,
    transport_factory: TransportFactory | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0

    path = args.config or os.environ.get("VAISOR_CONFIG") or "bench.yaml"
    try:
        config = load_bench(path)
        kwargs = {} if transport_factory is None else {"transport_factory": transport_factory}
        kwargs["base_dir"] = os.path.dirname(os.path.abspath(path))
        bench = Bench.from_config(config, **kwargs)  # type: ignore[arg-type]
    except BenchConfigError as exc:
        print(f"vaisor: error: {exc}", file=sys.stderr)
        return 2
    except (DriverError, TransportError, AuditError) as exc:
        print(f"vaisor: error: {exc}", file=sys.stderr)
        return 1

    handler = _cmd_devices if args.command == "devices" else _cmd_read
    try:
        return handler(bench, args)
    except UsageError as exc:
        print(f"vaisor: error: {exc}", file=sys.stderr)
        return 2
    except BenchConfigError as exc:
        print(f"vaisor: error: {exc}", file=sys.stderr)
        return 2
    except (DriverError, TransportError, AuditError, SafetyRefused) as exc:
        print(f"vaisor: error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        try:
            bench.close()
        except DriverError as exc:
            print(f"vaisor: error: {exc}", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
