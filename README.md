# pip

A small native Rust firmware for pip's physical interface on the Waveshare
ESP32-S3-Touch-AMOLED-1.8. ESP-IDF provides FreeRTOS and hardware drivers;
LVGL draws the controls. The first screen is an antialiased circle and `hello`
in Inter Variable. Tap the circle to alternate between warm white and pale blue.

This is a local display and card prototype. A host bridge can push notices and
decisions to pip, and receive button choices. It works over USB now and includes
an outbound Wi-Fi/TCP client. Connecting the actual ChatGPT Dot to the bridge's
local API remains future work; no ChatGPT credentials are used by this firmware.

## Five directions

1. **Desk companion.** Pip lives as a responsive dot. A tap reveals a short
   status; a second tap opens the relevant detail on the Mac. Start with a
   USB bridge and explicit idle/working/needs-attention states.
2. **Focus companion.** One intention and a timer ring. Tap to start/pause;
   pip quietly marks completion. The timer can work entirely on-device.
3. **Pocket inbox.** One short card at a time from pip. Swipe to acknowledge,
   defer, or open on the Mac. Cache a small bounded inbox for offline use.
4. **Voice pebble.** Hold to speak, release to send. The dot becomes a listening
   animation, then a short caption and spoken response. Requires audio bring-up
   and a host/network bridge; the board does not run a language model locally.
5. **Daily instrument.** An unobtrusive clock that reveals the next event or
   useful prompt when picked up. The IMU provides the gesture, the RTC the clock;
   synchronized content would come from the host.

The bridge/API for the actual Dot has not been established. These are proposed
interaction designs, not claims that an existing Dot API exposes these actions.

## Hardware and constraints

Recorded 2026-10-08 so development does not depend on `_archive/`.

| Item | Specification / observation |
| --- | --- |
| Board | Waveshare ESP32-S3-Touch-AMOLED-1.8 (from supplied project notes) |
| MCU | USB ROM query confirms ESP32-S3, QFN56 revision 0.2, dual Xtensa LX7, up to 240 MHz |
| Internal SRAM | 512 KiB total; code, RTOS, driver state and stacks consume part of it |
| PSRAM | ROM reports embedded 8 MiB, AP 3.3 V; octal, configured at 80 MHz |
| Flash | ROM detects 16 MiB, manufacturer 0x20, device 0x4018, quad 3.3 V |
| Security | ROM reports secure boot and flash encryption disabled |
| USB | Native USB Serial/JTAG; observed `/dev/cu.usbmodem21401`; detect after reconnect |
| Screen | Native 368 × 448 AMOLED, QSPI; mounted sideways in the enclosure, so UI is 448 × 368 landscape |
| V1 panel/touch | SH8601 / FT3168, no horizontal gap |
| V2 panel/touch | CO5300 / CST820, 16-pixel horizontal gap |
| Other hardware | AXP2101 PMIC, PCF85063 RTC, QMI8658 IMU, ES8311 mic/speaker, microSD, Wi-Fi/BLE |

The touch probe identifies the display variant before panel creation. Neither
controller responding is an error, not a reason to assume V1. The first firmware
boot detected the **V2 path**, address 0x15, controller ID 0xb7. Driver-family names FT5x06/CST816S
in the BSP refer to the compatible drivers, not necessarily the fitted chip name.

| Signal | GPIO |
| --- | --- |
| Display CLK / CS | 11 / 12 |
| Display D0 / D1 / D2 / D3 | 4 / 5 / 6 / 7 |
| Shared I2C SDA / SCL | 15 / 14 |
| Touch interrupt | 21 |
| Native USB D− / D+ | 19 / 20 |
| microSD CMD / CLK / D0 | 1 / 2 / 3 |
| Audio SCLK / MCLK / LRCLK | 9 / 16 / 45 |
| Audio DOUT / DIN / amplifier enable | 8 / 10 / 46 |

There is no desktop GPU. One RGB565 frame is 329,728 bytes (322 KiB).
Our two native-width 32-row DMA buffers total 47,104 bytes (46 KiB), allocated
internally. Software rotation adds one 23,552-byte scratch buffer, for 69 KiB
total pixel buffers. LVGL redraws dirty rectangles. Updates must start on even coordinates and cover
even pixel counts. AMOLED brightness is a panel command, not a PWM backlight.
The USB pins must remain available for flashing and recovery.

PSRAM has different access and bandwidth constraints from internal SRAM. Keep
DMA buffers, interrupt state and critical allocations internal. A full-screen
transfer at a hypothetical 40 MHz QSPI takes at least 16.49 ms before overhead;
this is not a measured frame rate. Prefer small animations on black and avoid
persistently bright static content. Runtime, battery life, cold-start behavior,
and physical display/touch behavior still require on-device verification.

The BSP example does not explicitly initialize all PMIC rails. Do not invent
voltage settings: validate an actual power-off/on and consult the board schematic
if cold-start fails. No SD, audio, radio, charger, or eFuse configuration is needed
for this first UI.

### Enclosure orientation

The panel is mounted 90° clockwise relative to the upright enclosure. Enable
`sw_rotate` in the LVGL display port and set `LV_DISPLAY_ROTATION_90`. In the
pinned port this maps logical pixels counterclockwise into native panel memory.
The logical UI becomes 448 × 368. Keep touch coordinates in native panel space:
LVGL's input processing automatically applies the inverse display transform.
Do not rotate touch a second time in the driver. Center controls using LVGL
alignment rather than hard-coded portrait coordinates.

Allow 30 ms after BSP panel initialization before starting LVGL. BSP 2.0.3's
sleep-out delay is only 100 ms, with no display-on settling delay. The vendor's
Arduino CO5300 reference allows 120 ms for sleep-out and 10 ms after its init
sequence. Startup is still being investigated: this extra delay alone did not
eliminate intermittent blank screens after USB resets. The current firmware
therefore reissues display-on **after** the 30 ms allowance and waits another
10 ms before drawing. Subsequent camera checks and the user's physical check
confirmed an upright, working display and touch. Genuine cold power-on still
needs testing.

For physical visual verification, the user provides a live camera preview in
QuickTime Player. Capture that window rather than treating a framebuffer dump
as proof of what the panel displays. **Never activate QuickTime or send keyboard
or mouse input**: the user is working on the same computer. The screenshot
helper's `--app` capture option activates the application. Instead, list windows
with `--list-windows --app 'QuickTime Player'`, then capture using only
`--window-id ID --mode temp`; that path does not activate the window.

## Build layout

- `src/main.rs`: Rust app, greeting, semantic cards and acknowledged choices.
- `src/network.rs`: bounded JSON-lines TCP client, reconnects and heartbeats.
- `components/pip_board/`: C17 hardware/LVGL bridge, screenshot and Wi-Fi setup.
- `assets/fonts/InterVariable.ttf`: original variable font, supplied by its author.
- `components/pip_board/inter_*.c`: generated ASCII glyphs, 20/28/40 px, weight 450,
  optical size 32, 4-bit coverage. Font axes are instantiated at build time;
  this initial renderer does not vary font weight at runtime.
- `sdkconfig.defaults`: 16 MiB flash, octal PSRAM, USB console, 240 MHz CPU.

Pinned starting stack: Rust `esp-1.97.0.0`, `esp-idf-sys` 0.38.1, ESP-IDF 5.5.5,
Waveshare BSP 2.0.3, `esp_lvgl_port` 2.6.2, LVGL 9.2.2. Keep `Cargo.lock` and
the generated `components_esp32s3.lock` in version control.

```sh
# First-time local helper installation (requires cargo, uv, node/npm).
sh scripts/bootstrap.sh

sh scripts/build.sh
.tools/python/bin/python scripts/device.py info
.tools/python/bin/python scripts/device.py flash
.tools/python/bin/python scripts/device.py monitor --reset --seconds 15

# Only needed after changing the font settings; generated C is checked in.
sh scripts/font.sh
```

The device script selects the sole Espressif 303a:1001 USB device, or accepts
`--port /dev/cu.usbmodem…` when multiple devices are connected. Monitoring is
bounded and closes the port when finished. Flashing checks the generated
application against the actual factory partition size before writing. USB
monitoring and screenshots suppress PySerial's DTR/RTS writes on open, which
otherwise reset the S3 on this Mac. Only `--reset` explicitly pulses reset.

Use the build script after C/font changes: it touches the bindings header to
make Cargo run CMake/Ninja, because esp-idf-sys does not track every extra
component source. Tooling lives under `.tools/` and `.embuild/`;
the initial build downloads ESP-IDF, its C compiler and managed dependencies.
The Espressif Rust compiler must already be installed with espup (ordinary
upstream rustup does not provide the Xtensa target). `LIBCLANG_PATH` defaults to
espup's `~/.espup/esp-clang`. Use `gmake` if invoking GNU Make on macOS.

ESP-IDF's headers use GNU inline assembly, so the bridge compiles as `gnu17`
(C17 with GNU extensions). Flash headers use ESP-IDF's generated settings:
the ROM starts in DIO, then the bootloader configures QIO.

## Bring-up results (2026-10-08)

- Release build succeeded with the pinned Rust/IDF/LVGL stack.
- Bootloader, partition table and application were flashed and hash-verified.
- Application image with cards, screenshots and Wi-Fi: 1,396,256 bytes, within the
  1,536,000-byte factory partition.
- The on-device 8 MiB PSRAM memory test passed.
- Touch probe: 0x15, ID 0xb7, selecting the V2 panel offset.
- LCD driver initialized; LVGL started; Rust printed `pip: ready`.
- Free heap after UI/screenshot setup, before network initialization:
  144,763 bytes internal; 8,048,776 bytes PSRAM.
- Initial portrait build ran for 15 seconds after reset without a crash or reboot;
  the user confirmed that tapping the circle changes its color.
- Landscape appearance verified through the live QuickTime camera; the user
  confirmed both upright text and working touch after rotation.
- Three consecutive direct screenshots passed length/CRC validation while
  preserving device uptime; each capture took approximately 1.2 seconds.
- A decision card sent through the host HTTP API rendered on the real device;
  its screenshot was retrieved through the bridge API.
- The user's physical "Looks good" selection was recorded once by the host.
- Wi-Fi was provisioned through the local password prompt; `/health` confirmed
  a TCP connection, and a subsequent notice delivered over Wi-Fi appeared in
  the device screenshot.
- Endpoint changed to `rmbm5.local:8787` without re-entering credentials; the
  device reconnected after a bridge restart using its saved hostname.
- After the final hostname firmware flash, direct captures still contain the
  card and TCP remains healthy, but background camera captures show a dark
  panel. A direct physical check is pending; do not treat a correct software
  capture as proof that the panel is displaying it. Startup reliability remains
  open until this discrepancy is resolved.
- Seven host tests cover screenshot integrity/color conversion, TCP
  authentication, reconnect/re-delivery, persistent choices and deduplication.
- The documented incremental build command also passed with `--locked`.

Logs are local and ignored: `.tools/build.log`, `.tools/flash.log`, `.tools/boot.log`.
Genuine cold power-on remains unverified. A board-driver warning
about I2C pull-ups and a panel pixel-format override warning appeared; neither
prevented controller identification or reaching the ready state.

Font source: installed Inter Variable 4.002, git-9bdd60c3a. SHA-256:
`e4205c4f6732a09891a43569397b59c53ee289ec915bf040d3425b4251ca2f94`.

## Pixel-exact screenshots

```sh
# When the bridge does not own the USB port:
.tools/python/bin/python scripts/device.py screenshot --output .tools/screen.png

# When running the bridge with --serial:
curl --fail http://127.0.0.1:8788/screenshot.png -o .tools/screen.png
```

`LV_EVENT_FLUSH_START` copies each dirty RGB565 rectangle before the display
port's rotation and byte swap. The resulting 448 × 368 shadow image contains
the pixels submitted to the display, in upright logical coordinates. This is
not a second rendering of the object tree. It is also not panel-memory readback:
use the camera to diagnose panel power/scanout failures and visible timing.

The shadow costs 329,728 bytes in PSRAM, with another 329,728 bytes temporarily
allocated during capture. The LVGL lock covers only the snapshot copy, so the UI
can animate during USB transfer. A CRC-32, dimensions, offsets, flush count and
device timestamp accompany the capture. The host rejects missing/corrupt data
and encodes PNG using Python's standard library. RGB565 expands to RGB888 by bit
replication. The initial font subset is printable ASCII.

Do not replace the bulk USB writes with `printf` per data line: console output
can silently drop bytes on its short timeout. Long transfers also yield CPU
time periodically so they cannot starve the idle task/watchdog.

## Host bridge and semantic cards

The host owns the queue, context and actions. Pip displays one card at a time
and returns stable card/option IDs. It never executes an email action itself.

```sh
# USB transport for immediate development, plus the TCP listener:
.tools/python/bin/python scripts/bridge.py --serial auto

# After Wi-Fi provisioning, TCP alone is enough:
.tools/python/bin/python scripts/bridge.py
```

The device TCP listener binds `0.0.0.0:8787`; the tooling HTTP API binds only
`127.0.0.1:8788`. Both ports are configurable. TCP uses newline-delimited JSON,
a randomly generated pairing token, 10-second heartbeats and reconnects.
The current TCP transport assumes a trusted LAN and does not use TLS.
The bridge token and durable queue/event history live in ignored, private
`.tools/bridge-config.json` and `.tools/bridge-state.json` files. Cards can
contain private content, so do not check these files into version control.

The HTTP API is a local tool integration point. Browser origins are rejected;
it is not a public website/API. No Dot connector or email integration has been
configured. Dot needs an authorized local tool capable of calling this API.

| Endpoint | Meaning |
| --- | --- |
| `GET /health` | Connection, active transport and pending count |
| `POST /cards` | Queue an immutable notice/decision; optional caller-supplied ID |
| `GET /cards` | Card history and pending/answered state |
| `GET /events?after=0` | Choice events with monotonic sequence numbers |
| `GET /screenshot.png` | Capture the screen while the bridge owns USB |
| `POST /configure` | USB provisioning; normally called by `device.py` |

Example request body for `POST /cards`:

```json
{
    "id": "example-decision-1",
    "kind": "decision",
    "title": "A question from pip",
    "body": "This is a test. Which option should I use?",
    "options": [
        {"id": "first", "label": "First option"},
        {"id": "second", "label": "Second option"}
    ]
}
```

Notices default to a Dismiss button. Decisions require 1–3 options. Display
text currently supports printable ASCII and newlines: title up to 60 characters,
body up to 600, option label up to 24. Long bodies scroll; long titles ellipsize.
The first renderer uses Inter 28 for headings and Inter 20 for body/buttons.

On connection, the device sends `{"type":"hello","version":1,"device":"pip",
"token":"..."}`. The server responds with `welcome`, then the pending card
(`type: card`). A tap sends `{"type":"choice","card_id":"...",
"option_id":"..."}`; the bridge saves it before returning an `ack` with the same
IDs. Duplicate choices produce one event. Pending cards survive a server restart
and are redelivered after reconnect; device replies retry until acknowledged.
An unacknowledged choice is currently held in device RAM and does not survive
device power loss. The host's persisted result prevents re-executing an already
recorded decision. External action handlers must also deduplicate by card ID.

### Wi-Fi provisioning

The ESP32-S3 needs a 2.4 GHz network. Run the bridge first, then use a local
terminal prompt to provision credentials over USB:

```sh
.tools/python/bin/python scripts/device.py configure \
    --host rmbm5.local --via-bridge
```

Use the Mac's Bonjour hostname rather than its DHCP address. This Mac's
`LocalHostName` is `rmbm5`; ESP-IDF's `CONFIG_LWIP_DNS_SUPPORT_MDNS_QUERIES=y`
makes standard hostname resolution query mDNS for `.local` names. The TCP client
resolves the hostname on each reconnect. The command prompts for SSID
and a hidden password; neither is passed on the shell command line. Credentials
and host settings are saved in the device's `pip` NVS namespace. The current
setup supports open networks and WPA2-compatible personal networks, not
enterprise authentication/captive portals. Omit `--via-bridge` when the bridge
does not own USB. A successful TCP connection takes over from the USB transport.
Check `/health` for `"transport":"tcp"` to verify the actual connection.

To change only the endpoint while retaining Wi-Fi credentials and the token:

```sh
.tools/python/bin/python scripts/device.py set-host --host rmbm5.local
```

This command uses USB; stop the bridge's USB transport first if it still owns
the port. It does not reset the device or require the Wi-Fi password again.

Tests:

```sh
.tools/python/bin/python -m unittest discover -s scripts -p 'test_*.py'
cargo fmt --check
sh scripts/build.sh --locked
```

## Recovery

Firmware replacement is authorized; no factory backup is required for this
project. Flashing leaves eFuses untouched. If automatic ROM-loader entry fails,
hold BOOT while resetting/powering on, release BOOT, then rediscover the USB port.
Do not permanently disable USB Serial/JTAG or repurpose GPIO19/20.

## References

- [Waveshare hardware and revisions](https://docs.waveshare.com/ESP32-S3-Touch-AMOLED-1.8)
- [Vendor examples and setup](https://github.com/waveshareteam/ESP32-S3-Touch-AMOLED-1.8/tree/78e13f852929c2ab4f9d5e0ad1c50ea378dbf2b4)
- [Waveshare BSP 2.0.3](https://components.espressif.com/components/waveshare/esp32_s3_touch_amoled_1_8/versions/2.0.3)
- [Rust ESP-IDF build options](https://github.com/esp-rs/esp-idf/blob/master/esp-idf-sys/BUILD-OPTIONS.md)
- [LVGL font format](https://lvgl.io/docs/open/9.2/overview/font)

The new app is independent of Playbit; the archived document was used only as
a board reference.
