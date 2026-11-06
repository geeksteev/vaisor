# vAIsor

**vAIsor** (pronounced "visor") gives AI hands and eyes on a real electronics bench: multimeters,
cameras, power supplies, flashers, relays. Any model can drive it through MCP, a REST API, or the
Python library. It is for garage and basement labs of any size: one cheap meter, or a rack of gear.

Code here is written by a builder agent, reviewed by a reviewer agent, and merged automatically when
CI and the review pass. `ROADMAP.md` lists the goals in order; the planner works through them.

## The user experience we are building
```
git clone https://github.com/geeksteev/vaisor && cd vaisor
cp .env.example .env          # API keys, only needed for AI vision features
cp bench.example.yaml bench.yaml   # describe your instruments
vaisor up                     # starts the bench service (MCP + REST)
```

## Architecture
- **Core package `vaisor/`**: device model, config loading, driver registry, safety layer, audit log.
- **Drivers** live in `vaisor/drivers/<class>/<vendor>_<model>.py`, one per instrument model, grouped by
  class (`multimeter`, `camera`, `psu`, `scope`, `logic`, `flasher`, `relay`). A driver declares
  **capabilities** (e.g. `read_voltage_dc`, `read_current_dc`, `capture_frame`) and nothing else
  knows about vendor protocols.
- **Drivers are discovered through Python entry points** (`vaisor.drivers`), so third-party packages
  can add instruments without touching this repo.
- **`bench.yaml`** describes one bench: named device instances, their driver, connection and limits.
  Two meters of different makes are simply two entries. The schema is validated at startup with clear errors.
- **Front doors** (all thin layers over the same core): MCP server (stdio and HTTP), REST API with
  OpenAPI, Python library, CLI (`vaisor`). Never put logic in a front door that the others would miss.
- **Generic adapters** wrap existing ecosystems instead of rewriting them: sigrok (hundreds of DMMs,
  logic analyzers, scopes) and PyVISA/SCPI (lab PSUs and scopes).
- **Model-agnostic**: any AI feature (e.g. judging a camera frame) calls a provider configured in `.env`
  (Anthropic, OpenAI, or any OpenAI-compatible local endpoint). No provider is hardcoded.

## Safety rules (binding)
- **Safety lives in the core, not in drivers or models.** Every actuating command (power on, set voltage,
  move, flash) passes through the safety layer, which enforces the limits in `bench.yaml`.
- One controller at a time: an exclusive lock per bench; read-only access may be shared.
- A software **e-stop** turns off every actuating device; if a relay device is configured, it cuts power too.
- Every command is appended to an audit log (JSON lines) with time, caller, device, arguments and result.
- Defaults are safe: outputs off at startup, no actuation without explicit limits in config.
- Network front doors require a token. Never bind to 0.0.0.0 without auth.

## Contributing a driver (the contract)
1. `vaisor/drivers/<class>/<vendor>_<model>.py` implementing the class interface.
2. `examples/devices/<vendor>_<model>.yaml`: a sample `bench.yaml` entry.
3. Tests that run **without hardware**: recorded bytes or frames in `tests/fixtures/<vendor>_<model>/`
   replayed through a fake transport. Protocol details from the manual go in the driver docstring.
4. A row in `docs/hardware.md` (the supported-hardware list).

## Rules for agents
- Python 3.11+, typed, `pytest` for tests; nothing in CI may require hardware or network.
- Serial, USB and camera access always go through a transport abstraction so they can be faked.
- No secrets, real hostnames or IPs in the repo. `.env` and `bench.yaml` are gitignored; commit `.example` files.
- `.github/` and `LICENSE` are human-owned.
