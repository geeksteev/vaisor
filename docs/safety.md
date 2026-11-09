# Safety

Safety lives in the core (`vaisor/safety.py`, `vaisor/bench.py`), not in drivers or models. Every
command from every front door goes through `Bench.run`, which audits, locks and checks limits
before a driver method is called.

## Limits per class
Limits live in `bench.yaml` under each device's `limits`. No limits means no actuation.

| Class | Keys | Rule |
|-------|------|------|
| `psu` | `max_voltage` (>0), `max_current` (>0), `min_voltage` (default 0) | `set_voltage` must be within `[min_voltage, max_voltage]`; `set_current` within `[0, max_current]`; `output_on` needs valid limits |
| `relay` | any non-empty mapping | `relay_set` needs limits configured, and `closed` must be a bool |
| other | none yet | `flash` is always refused; `move` needs a motion limits model that no class has yet |

Numbers must be finite; NaN, infinity, booleans and strings are refused.

## Refusal codes
A refusal raises `SafetyRefused` with a machine-readable `code`, and is audited with `refusal_code`.

| Code | Meaning |
|------|---------|
| `no_limits` | No usable limits are configured for an actuating capability |
| `out_of_range` | A value is outside the configured limits |
| `invalid_argument` | Missing, unexpected, non-numeric or non-finite arguments |
| `unknown_capability` | The capability is not a non-empty string, or the device lacks it |
| `estopped` | The e-stop is latched |
| `lock_timeout` | The bench lock could not be taken in time |
| `unknown_device` | The device name is not in `bench.yaml` |

## Lock
One controller at a time: actuating commands take an exclusive lock on `<state_dir>/bench.lock`.
Read-only commands take a shared lock, so any number of readers may run together, but never
alongside a writer. If the lock cannot be obtained within the timeout the command is refused with
`lock_timeout`. A lock whose holder pid is dead (same host) or older than the stale timeout is
recovered automatically, and the takeover is audited as a `lock_takeover` record.

## E-stop and latch
`Bench.estop` writes the latch file `<state_dir>/estop.json`, calls `output_off` on every device
that has it, then opens the configured `estop_relay` (`relay_set` with `closed=false`). It never
takes the lock and continues past individual failures. While latched, actuating commands are
refused with `estopped`; reads and `output_off` still work. `Bench.clear_estop` removes the latch.
An unreadable latch counts as latched. Outputs are also switched off when a device is first opened.

## Audit log
`<state_dir>/audit.jsonl`, one JSON object per line. A command writes an `attempt` record, then one
final record: `ok`, `error` or `refused` (with `refusal_code`). Refusals before the attempt (unknown
device or capability, latched e-stop) have only the `refused` record. Argument keys containing
`token`, `password`, `secret` or `key` are redacted. If the log cannot be written, the command does
not run.

```json
{"ts": "2026-01-01T12:00:00+00:00", "caller": "cli", "command": "set_voltage", "outcome": "refused", "device": "psu1", "args": {"voltage": 99.0}, "refusal_code": "out_of_range", "error": "voltage 99.0 outside [0, 12.0] for 'psu1'"}
```

## Declaring actuating capabilities
A driver lists its capability names in `capabilities` and implements a method of the same name.
The names `set_voltage`, `set_current`, `output_on`, `relay_set`, `move` and `flash` are actuating
and are always checked against limits; `output_off` is always allowed. A driver that offers
`output_off` is switched off when first opened and by the e-stop. Drivers must not replace the
core's limit checks with their own. See `tests/fake_actuators.py` for examples.
