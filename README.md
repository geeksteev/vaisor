# vAIsor

**An AI visor for your bench.** *(pronounced "visor")*

vAIsor gives any AI model hands and eyes on a real electronics bench: read your multimeter, watch
your board through a webcam, flash firmware, switch power, all through MCP, a REST API, or Python,
with safety limits the AI can't override.

Built for garage and basement labs of any size: one cheap meter, or a rack of gear.

> **Status:** early development. See [`ROADMAP.md`](ROADMAP.md).

## Quickstart
```bash
git clone https://github.com/geeksteev/vaisor && cd vaisor
pip install -e .
cp .env.example .env               # API keys, only for AI vision features
cp bench.example.yaml bench.yaml   # describe your instruments, then edit the serial port
vaisor devices                     # list the devices in bench.yaml
vaisor read dmm1                   # one reading
vaisor read dmm1 --watch --jsonl   # stream readings, one JSON object per line
```

`vaisor up` (the MCP and REST front doors) is coming in a later roadmap goal.

### Linux serial permissions
Serial adapters are usually owned by the `dialout` group. Add yourself, then log out and in again:
```bash
sudo usermod -aG dialout $USER
```

### bench.yaml
`bench.yaml` describes one bench: a `version`, a `name`, and named `devices`. Each device has a
`class` (e.g. `multimeter`), a `driver` (`<vendor>_<model>`), a `connection` (serial port and
settings) and optional `limits` for the safety layer. Two meters of different makes are simply two
entries. See the commented [`bench.example.yaml`](bench.example.yaml) and `examples/devices/`.
The file is validated at startup with clear errors. Use `--config PATH` or `VAISOR_CONFIG` to point
at another file.

## Safety
Limits, the bench lock, the e-stop and the audit log are described in [`docs/safety.md`](docs/safety.md).

## Supported hardware
See [`docs/hardware.md`](docs/hardware.md). Adding your instrument is a driver, a sample config, and
tests with recorded data: no hardware needed in CI.

## Development
```bash
pip install -e ".[dev]"
pytest
```

See [`ROADMAP.md`](ROADMAP.md) for what is planned next.
