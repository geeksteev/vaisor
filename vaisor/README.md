# vAIsor

**An AI visor for your bench.** *(pronounced "visor")*

vAIsor gives any AI model hands and eyes on a real electronics bench: read your multimeter, watch
your board through a webcam, flash firmware, switch power, all through MCP, a REST API, or Python,
with safety limits the AI can't override.

Built for garage and basement labs of any size: one cheap meter, or a rack of gear.

> **Status:** early development. See [`ROADMAP.md`](ROADMAP.md).

## Quickstart (once the first release lands)
```bash
git clone https://github.com/geeksteev/vaisor && cd vaisor
cp .env.example .env               # API keys, only for AI vision features
cp bench.example.yaml bench.yaml   # describe your instruments
vaisor up
```

## Supported hardware
See [`docs/hardware.md`](docs/hardware.md). Adding your instrument is a driver, a sample config, and
tests with recorded data: no hardware needed in CI.
