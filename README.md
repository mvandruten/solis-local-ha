# Solis Inverter Local (HACS)

Fully **local** readout of your Solis inverter datalogger (S3-WIFI-ST /
S3-GRPS-ST) for Home Assistant — **no SolisCloud account, no API keys, no
internet required**. This integration talks directly to the datalogger's
built-in status page (`http://<datalogger-ip>/inverter.cgi`) over your LAN.

| | |
|---|---|
| 📡 | reads serial, firmware, model, temperature, live power, yield today, total yield, alerts |
| 🔒 | zero cloud dependencies — works with your internet down |
| 🌙 | understands night mode (the datalogger is powered by the inverter) |
| ⚡ | feeds the Energy dashboard "Solar production" directly |

## Installation (HACS)

1. HACS → **⋮ (three dots)** → **Custom repositories**
2. URL: `https://github.com/mvandruten/solis-local-ha`, Category: **Integration** → Add
3. The **Solis Inverter Local** card appears in HACS → **Download**
4. Restart Home Assistant
5. **Settings → Devices & Services → Add Integration → Solis Inverter Local**

*(Manual install: copy `custom_components/solis_local/` into your HA
`config/custom_components/` directory and restart.)*

## Configuration

The setup flow asks for:

| Field | What it is |
|---|---|
| **Datalogger IP address** | e.g. `192.168.1.228` — find it in your router's client list |
| **Datalogger password** | the admin password of the datalogger (same one used for its web UI / the SolisCloud "device password") |
| Poll interval | seconds between read cycles (default **300**, matching the stick's own ~5-min read loop) |
| Read timeout | max seconds to wait per read attempt (default 10) |
| Refresh mode | how fresh data is obtained (default **watch**) |

After setup, **Configure** on the integration (⋮ → Options) lets you change
poll interval / timeout / refresh mode without re-entering the password.

> **Tip — give it a fixed address.** The datalogger's own static-IP page is
> known to silently drop the IP unless IP/mask/gateway are all filled in, and
> it has no DNS field. **Prefer a DHCP reservation for the datalogger's MAC
> address in your router** over a static IP on the device.

### Refresh modes

The datalogger serves `inverter.cgi` from a RAM buffer that only holds
populated data for **~1 second** around each read — the rest of the time it
returns an all-zero placeholder. On its own the stick re-reads the inverter
roughly every **5 minutes**. The refresh mode only changes *when* you get
fresh data:

| Mode | How | Fresh after |
|---|---|---|
| `watch` (default) | no kick — waits for the stick's own read cycle and catches the populated window | up to one stick cycle (~5 min) |
| `reboot` | reboots the datalogger via its local restart page; it re-reads the inverter on boot | ~25 s (costs a reboot) |
| `none` | single best-effort read; gives up fast | whenever the buffer happens to be populated |

**`watch` is recommended**: zero kicks, zero wear, zero internet. At night the
inverter powers down and takes the datalogger with it — the integration then
flips the *Inverter online* binary sensor off and the *Data age* sensor grows.
At the first poll after local midnight it resets the day counters (Yield today
→ 0, Current power → 0) instead of carrying yesterday's values into the
morning; the lifetime *Total yield* is never reset. No errors, no retry spam.

## Entities

| Entity | Device class | Unit | Notes |
|---|---|---|---|
| Current power | power | W | live generation |
| Yield today | energy | kWh | `total_increasing` → Energy dashboard; resets to 0 at local midnight |
| Total yield | energy | kWh | `total_increasing`; `unavailable` until the stick reports a confirmed total |
| Inverter temperature | temperature | °C | |
| Last updated | timestamp | — | when the readout was taken |
| Data age | — | s | how long since the current state was produced |
| Inverter model / Firmware / Serial | — | — | diagnostics |
| **Inverter online** (binary) | connectivity | — | off at night when the inverter powers down |
| **Alerts** (binary) | problem | — | on when the datalogger reports YES |

**Energy dashboard:** Settings → Energy → *Solar production* → add **Yield
today**. Long-term statistics accumulate hourly, so give it a day before
judging the curve.

**Service:** `solis_local.force_refresh` re-reads the page immediately
(nice for a dashboard button or an automation after "inverter started" — see
the grid-import sensor or your battery controller for a trigger).

## Troubleshooting

- **"Cannot reach the datalogger"** during setup — is it powered? Same
  subnet/VLAN? Try `ping <ip>` and open `http://<ip>/inverter.cgi` in a
  browser (it asks for a password — that's the one you need).
- **"The datalogger rejected the password"** — the Basic-auth password for
  `inverter.cgi` is the datalogger's admin/device password.
- **Total yield shows unknown/unavailable** — the datalogger reports the
  total as `u` (unknown) until it has a confirmed value; this resolves by
  itself, then the sensor stays available.
- **Values don't change for ~5 minutes** — that's `watch` mode working as
  intended; `reboot` or `force_refresh` gives faster data.
- **Everything bounces every night** — expected: the stick is
  inverter-powered. At local midnight the day counters reset to 0, and Home
  Assistant keeps those values until the morning read; automations should key
  off *Inverter online* / *Data age* rather than raw values.
- **Multiple dataloggers** — supported: add the integration once per
  datalogger.

## Development

```bash
uv sync
uv run pytest          # parser + model tests (no HA needed)
uv run ruff check .
uv run python scripts/smoke_local.py   # live read against a real datalogger
```

The component's pure logic (parser, models, LAN API) has zero
`homeassistant` imports so it can be tested and smoke-verified without a
running HA. Research, protocol notes and the original CLI poller live in the
[research repository](https://github.com/mvandruten/soliscloud_local_test).

## License

MIT