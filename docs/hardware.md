# Supported hardware

| Class | Vendor | Model | Driver | Connection | Capabilities | Notes |
|-------|--------|-------|--------|------------|--------------|-------|
| multimeter | Extech | 383273 | `extech_383273` | serial, 9600 8N1 | `read_measurement` | RTS must be held low or the meter stays silent. Polled by sending a single SPACE (0x20); no other bytes are ever sent, since the meter treats `9421EGNRWUSTX` as commands. The function/range table needs verification against the manual. |
