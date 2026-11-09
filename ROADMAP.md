# vAIsor roadmap

The planner agent files these goals one at a time, in order: when no goal is open, it files the next
`## Goal:` section that has not been filed yet. Edit freely; only sections starting with `## Goal:` are read.

## Goal: Core, bench.yaml and the first driver
Create the `vaisor` package and everything needed to read a real instrument from the CLI.

- Device model: a device instance has a name, a class, a driver, connection settings and capabilities.
- `bench.yaml` schema (pydantic or similar) with clear validation errors; `bench.example.yaml` committed, `bench.yaml` gitignored.
- Driver registry using the `vaisor.drivers` entry-point group; built-in drivers registered the same way.
- Serial transport abstraction with a fake transport for tests.
- First driver: **Extech 383273 multimeter** (`vaisor/drivers/multimeter/extech_383273.py`). Protocol:
  9600 8N1, RTS must be held LOW or the meter stays silent. Poll by sending one SPACE (0x20); never send
  the reserved bytes `9 4 2 1 E G N R W U S T X`. Reply is 5 bytes `02 | function/range | lo | hi | 03`.
  Digits word = `hi << 8 | lo`: bit 0 polarity (1 = positive, 0 = negative; for frequency 0 = kHz, 1 = MHz), bit 1 leading digit (0/1),
  bits 2-5, 6-9, 10-13 the next three digits with **each 4-bit field stored MSB-first**, bits 14-15 decimal
  point (00 none, 01 `000.0`, 10 `00.00`, 11 `0.000`). The manual's example `02 0C 21 B1 03` must decode to +12.3 kΩ (function code 0x0C = OHM 200 kΩ).
  Function codes include 0x00-0x04 DCV 200mV/2V/20V/200V/1000V, 0x21 DCA 20A, 0x22 DCA 200mA, 0x24 DCA 20mA,
  0x28 DCA 2mA, 0x30 DCA 200µA, 0x80-0x84 ACV, 0x08-0x11 OHM ranges; mark the table "verify against manual".
  Digits outside 0-9 mean overrange/special: report value as None.
- CLI: `vaisor devices` (list configured devices and capabilities), `vaisor read <device>` (one reading),
  `vaisor read <device> --watch --jsonl` (stream JSON lines).
- `.env.example`, `README.md` quickstart, `docs/hardware.md` with the first row.

## Goal: Safety layer and audit log
Every actuating command goes through one safety layer before any driver sees it.

- Per-device limits from `bench.yaml` enforced in the core (max voltage, max current, allowed ranges, motion bounds).
- Exclusive controller lock per bench (with timeout and stale-lock recovery); read-only access shared.
- Software e-stop: one call turns off every actuating device; optional relay device cuts power.
- Safe defaults: outputs off at startup; actuation refused if the device has no limits configured.
- Audit log: JSON lines with time, caller, device, command, arguments, result, and refusals.
- Tests prove refusals, e-stop and lock behaviour without hardware.

## Goal: MCP server
Expose the bench to any MCP-capable AI app.

- Tools generated from device capabilities (e.g. `read_voltage_dc(device)`, `capture_frame(device)`), plus
  `list_devices`, `bench_status`, `estop`.
- Transports: stdio (for local clients) and streamable HTTP with bearer-token auth.
- `vaisor up` starts it; config via `bench.yaml` + `.env`.
- Docs: connecting from Claude Desktop, Claude Code, and other MCP clients.
- Tests with an in-process MCP client against fake devices.

## Goal: REST API and Python client
Same capabilities for models and scripts that don't speak MCP.

- HTTP API with an OpenAPI spec (usable directly as tool definitions for any function-calling model).
- Token auth, same safety layer and audit log as MCP.
- `vaisor.client` Python client.
- Example: a 30-line script that lets any OpenAI-compatible model call the bench via the OpenAPI tools.

## Goal: Camera and motion detection
Give the AI eyes on the bench.

- UVC camera driver (OpenCV/V4L2) with fixed exposure, focus and white balance from `bench.yaml`.
- `capture_frame` returns JPEG plus metadata; MCP tool returns it as an image.
- Deterministic motion utilities: frame differencing, "did it move", "has it settled", region-of-interest support.
- Optional AI judgement: `ask_about_frame(device, question)` using the provider configured in `.env`
  (Anthropic, OpenAI, or OpenAI-compatible). No provider hardcoded.
- Tests with recorded frames.

## Goal: Flasher and serial console
Let the AI load firmware and watch it boot.

- Flasher driver for ESP32-class boards (esptool; PlatformIO upload when a project path is given).
- Serial console device: capture boot logs with timeout, search for patterns.
- Both go through the safety layer (flashing is an actuating command) and the audit log.
- Tests with fake transports.

## Goal: Ecosystem adapters and contributor kit
Support hundreds of instruments without writing every driver.

- sigrok adapter driver (via `sigrok-cli`): DMMs and logic analyzers it supports become vAIsor devices.
- PyVISA/SCPI adapter driver for lab PSUs and scopes, with per-model capability profiles.
- `docs/contributing-drivers.md`, a driver template, and a fixture-recording helper
  (`vaisor record <device>` captures raw bytes for tests).

## Goal: Easy deployment
Clone, configure, run, on anything from a Raspberry Pi to a lab PC.

- `vaisor up` as a systemd service installer for Linux (Raspberry Pi OS 64-bit included).
- Docker image with device passthrough docs.
- `vaisor doctor`: checks ports, permissions (dialout/video groups), config, and connectivity.

## Goal: Hardware-in-the-loop toolkit
Let agents verify real hardware behaviour, not just compiled code.

- Scripted test runner: a YAML test plan of steps (send command, wait, capture, measure, assert).
- Reports: JSON plus key frames and measurement traces, pass/fail per step.
- Calibration helper: step an actuator slowly, stop on a current spike or when the image stops changing,
  back off a margin, and output a **proposal** file with evidence. Never writes limits itself.
