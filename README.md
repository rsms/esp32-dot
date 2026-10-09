# esp32-dot

Native Rust firmware for Dot's physical interface, using ESP-IDF and LVGL.
Currently targets the Waveshare ESP32-S3-Touch-AMOLED-1.8.
ESP-IDF provides FreeRTOS and hardware drivers; LVGL draws the controls.
The initial hello-world has evolved into the Figma v1 companion faces and
paginated messages in Inter Variable. See the v1 section below for behavior.

This is a local display and card prototype. A host bridge pushes notices and decisions to Dot and receives replies over
Wi-Fi/TCP at `<MAC_HOSTNAME>.local:8787`. USB remains available for flashing and captures.
The bridge now exposes MCP tools and signed reply events for ChatGPT Dot. Local
MCP and hardware round trips are tested; cloud connection requires the account's
Secure MCP Tunnel and plugin setup below. No ChatGPT credentials go on the device.

esp32-dot was designed by rsms and built in collaboration with ChatGPT.

## Five directions

1. **Desk companion.** Dot lives as a responsive dot. A tap reveals a short
   status; a second tap opens the relevant detail on the Mac. Start with a
   USB bridge and explicit idle/working/needs-attention states.
2. **Focus companion.** One intention and a timer ring. Tap to start/pause;
   Dot quietly marks completion. The timer can work entirely on-device.
3. **Pocket inbox.** One short card at a time from Dot. Swipe to acknowledge,
   defer, or open on the Mac. Cache a small bounded inbox for offline use.
4. **Voice pebble.** Hold to speak, release to send. The dot becomes a listening
   animation, then a short caption and spoken response. Requires audio bring-up
   and a host/network bridge; the board does not run a language model locally.
5. **Daily instrument.** An unobtrusive clock that reveals the next event or
   useful prompt when picked up. The IMU provides the gesture, the RTC the clock;
   synchronized content would come from the host.

These are interaction directions. The implemented interface and integration
status are described below; microphone audio remains future work.

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
even pixel counts. AMOLED brightness is a panel command, not a PWM backlight. Startup brightness
is 100% (requested by the user).
The USB pins must remain available for flashing and recovery.

PSRAM has different access and bandwidth constraints from internal SRAM. Keep
DMA buffers, interrupt state and critical allocations internal. A full-screen
transfer at the BSP's configured 40 MHz QSPI takes at least 16.49 ms before overhead;
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

The V2 panel needs the complete Waveshare Arduino reset sequence. After BSP
creation, reset it again, wait **200 ms**, send sleep-out, wait **120 ms**, then
program its controls/pixel format, display-on and brightness, and wait 10 ms.
The BSP/driver's 80 ms reset delay and programming-before-wake ordering produced
blank physical screens despite correct LVGL captures. The earlier 30 ms delay
and repeated display-on were insufficient. Replaying the V2 reference sequence
restored the physical display, confirmed by the user and a background camera
capture. Genuine cold power-on still needs testing. The override applies only
to the probed V2 board; the existing V1 startup remains separate.

For physical visual verification, the user provides a live camera preview in
QuickTime Player. Capture that window rather than treating a framebuffer dump
as proof of what the panel displays. **Never activate QuickTime or send keyboard
or mouse input**: the user is working on the same computer. The screenshot
helper's `--app` capture option activates the application. Instead, list windows
with `--list-windows --app 'QuickTime Player'`, then capture using only
`--window-id ID --mode temp`; that path does not activate the window.

## Build layout

- `src/main.rs`: Rust app, semantic cards, state commands and acknowledged choices.
- `src/network.rs`: bounded JSON-lines TCP client, reconnects and heartbeats.
- `components/pip_board/`: C17 hardware/LVGL bridge, screenshot and Wi-Fi setup.
- `assets/fonts/InterVariable.ttf`: original variable font, supplied by its author.
- `components/pip_board/inter_*.c`: generated ASCII glyphs, 20/28/40 px, weight 450,
  optical size 32, 4-bit coverage. The v1 message font adds 55 px at weight 500,
  optical size 27.5, plus a separate 88 px checkmark glyph. Font axes are instantiated at build time;
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
bounded and closes the port when finished. Flashing generates the custom table from `partitions.csv` and checks the
application against its factory partition size before writing. USB
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
- Endpoint changed to `<MAC_HOSTNAME>.local:8787` without re-entering credentials; the
  device reconnected after a bridge restart using its saved hostname.
- The earlier blank-panel discrepancy was confirmed physically. The v1 pass
  fixed it using the vendor's complete V2 reset/wake ordering; both the user
  and a later live camera capture confirmed visible, upright artwork.
- Eight host tests cover screenshot integrity/color conversion, TCP
  authentication, reconnect/re-delivery, persistent choices, interaction
  deduplication and error-card validation. The hardware UI smoke test also
  passes state changes, pagination, back navigation, queue advancement and
  acknowledged dismissals.
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

The host owns the queue, context and actions. Dot displays one card at a time
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
it is not a public website/API. Dot uses the MCP adapter described below;
email access remains a separate Dot plugin connection.

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
    "title": "A question from Dot",
    "body": "This is a test. Which option should I use?",
    "options": [
        {"id": "first", "label": "First option"},
        {"id": "second", "label": "Second option"}
    ]
}
```

Notices use a dismissal page. Decisions require 1–3 options. Display
text currently supports printable ASCII and newlines: title up to 60 characters,
body up to 600, option label up to 64. Titles and bodies paginate together.
Choices use the Figma renderer described below.

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
    --host "<MAC_HOSTNAME>.local" --via-bridge
```

Use the Mac's Bonjour hostname rather than its DHCP address. Find it with
`scutil --get LocalHostName`; ESP-IDF's `CONFIG_LWIP_DNS_SUPPORT_MDNS_QUERIES=y`
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
.tools/python/bin/python scripts/device.py set-host --host "<MAC_HOSTNAME>.local"
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

## Figma v1 renderer (2026-10-08)

The authoritative v1 is the flow in the user's 13:45 screenshot, with source
frames listed in [assets/figma/README.md](assets/figma/README.md). Other areas
of the Figma file are WIP, except the subsequently approved reply-choice flow
`4:1568` supplied in the user's 15:37 screenshot.

`components/pip_board/pip_ui.c` renders sleeping, idle, listening, thinking and
attention faces using the original Figma vectors. Messages use Inter Medium
55 px (2× the design), optical size 27.5, 64 px line spacing and cap-height-aligned positioning.
The display is RGB565, so colors and antialias coverage are quantized.

Messages wrap using LVGL's own font metrics into four-line pages. An optional
`title` becomes the first paragraph; omit it for an uninterrupted message.
Left/right 128 px strips navigate. The last text page advances to a checkmark
screen; its center region dismisses. Message dots exclude the dismissal page,
matching Figma. Long messages show a sliding window of up to nine dots.
A new card shows attention for 900 ms (tap skips ahead). Dismissal retains the
existing retry/ACK protocol; an acknowledged card advances the host queue or
returns to idle. `kind: "error"` uses the same flow in #a44200.

Decisions use the same text pages, followed by one white inset card per choice.
The page row combines text-page dots with A/B/C markers. Tapping a choice opens
a separate confirmation screen; only a tap inside its white circle submits.
Horizontal swipes move between pages. On choice screens, taps in the blue
left/right margins also navigate; on confirmation screens, the arrows navigate
and cancel the tentative selection. Forward at the final choice stays on that
choice, and back from choice A returns to the final text page. Vertical drags
do not select or confirm. Labels use Inter Medium 55 px and are centered within
the 400×256 card; unusually long labels shrink to fit rather than being clipped.
Confirmation uses a 192 px circle, Inter SemiBold 22 px caption, and Bold 33 px
choice markers. The device emits its existing durable choice reply only after
confirmation, so the MCP/event protocol is unchanged.

The reply-choice firmware was built, flashed with hash verification, and
checked using CRC-verified device screenshots against the Figma reference.
The hardware tests passed for 1/2/3 choices, backward and forward navigation,
confirmation cancellation, and prevention of premature replies. The existing
notice/error UI smoke test and all 18 host tests also passed. Hardware tests
use simulated touch. The user also verified physical swipes, choice taps, and
confirmation on the device.

The user observed slow top-to-bottom redraws and tearing. Profiling found that
the touch handler was subscribed to draw events as well as input events, causing
repeated synchronous LVGL warnings during rendering. It now subscribes only to
press/release/press-lost events. Redundant child corner clipping was removed
(all current content is inset within the rounded background), and LVGL's C
rasterizer is built with `-O2` rather than the default `-Os`.

Measured full-screen face redraws dropped from 225–236 ms to 76–83 ms, with
pixel-identical captures for all five faces. These are median LVGL refresh
durations over three samples per state, including software rotation, screenshot
buffer copies, and transfer submission/waits. The final asynchronous DMA
completion may occur after the measured refresh ends. This is about a 3×
improvement, not proof of 30 FPS animation or tear-free panel scanout. The
current path still sends 15 partial strips per full redraw; transfer batching,
rendering cost, and panel synchronization remain work for animated transitions.

Reproduce the measurement with an empty device queue:

```sh
.tools/python/bin/python scripts/render-bench.py --output .tools/render-bench
```

The test cycles the face states, records timings, and captures each state.
The separate sleep benchmark below measures sustained animated refreshes.
The USB `render-stats` command reports the last nonempty refresh: `refresh_us`
is elapsed time, `flush_us` is time in flush callbacks (including copies and
rotation), `wait_us` is time waiting for a previous transfer, and `strips` and
`pixels` describe the workload. It does not stream logging during rendering.

Tapping sleeping/idle shows listening and emits a `listen_start` interaction;
tapping again emits `listen_stop` and returns to idle. Microphone recording
is not implemented yet.

Idle transitions to sleeping after 10 seconds without interaction.
Entering idle starts a fresh timeout; touch activity resets it, and leaving
idle cancels it. An incoming card therefore prevents sleep while it is being
read. Holding a finger down also prevents sleep. Network heartbeats and
diagnostic inspection do not count as user activity. Tapping sleeping still
starts listening. This is a UI state transition, not MCU deep sleep.
Interaction events are best-effort notifications; unlike card replies they
are not retried. Their unique IDs let the host deduplicate received events.

```sh
# Show a companion state when the card queue is empty.
curl --fail http://127.0.0.1:8788/state \
    -H 'Content-Type: application/json' -d '{"state":"sleeping"}'

# Show a paginated message (omit title to avoid an extra paragraph).
curl --fail http://127.0.0.1:8788/cards \
    -H 'Content-Type: application/json' \
    -d '{"kind":"notice","body":"A message from Dot."}'

# Hardware smoke test: requires TCP connected and an empty queue.
.tools/python/bin/python scripts/ui-smoke.py
.tools/python/bin/python scripts/choices-smoke.py
.tools/python/bin/python scripts/idle-smoke.py
```

The hardware test generates and dismisses its own notice/error cards, checks
page boundaries and back navigation, verifies host replies, and saves CRC-
validated screen captures under `.tools/v1/`. The choice test covers 1–3
options, swipe boundaries, tentative-selection cancellation, and exactly one
reply after confirmation, with captures under `.tools/choices/`.
The idle test checks the 10-second timeout, activity reset, wake tap, and
incoming-message interruption, with captures under `.tools/idle/`.
USB JSON commands `inspect`, `tap`, and `drag` expose UI state and invoke the
same navigation handler as physical touch. A drag has `x0`, `y0`, `x1`, `y1`;
they are development tools, not evidence that the touch hardware was tapped.

The v1 artwork exceeds the original app partition. `partitions.csv` gives the
factory app 4 MiB at 0x10000 and keeps NVS/PHY addresses unchanged. The flashing
script generates and validates that real table; esp-idf-sys uses its temporary
project's default table during the intermediate build. LVGL uses ESP-IDF's C
allocator so large image-decoder buffers can use PSRAM instead of exhausting
LVGL's former 64 KiB fixed pool. DMA display buffers remain internal.

Final v1 validation: 1,669,456-byte firmware in the 4 MiB app partition; eight
host tests and the hardware UI smoke test pass. The final idle/listening screens
were also verified through background QuickTime captures. Font comparison
against the Figma reference places the first message's line extents within
1–2 device pixels (font rasterization/RGB565 differences remain). A transient
post-flash TCP disconnect interrupted one run; the full run passed after Wi-Fi
settled. The bridge reconnects automatically.

Panel startup reference:
[Waveshare Arduino CO5300 driver](https://github.com/waveshareteam/ESP32-S3-Touch-AMOLED-1.8/blob/main/examples/arduino-v2/libraries/GFX_Library_for_Arduino/src/display/Arduino_CO5300.cpp)
and its adjacent header's initialization table. The USB `panel` diagnostic
prints raw SPI read attempts; all-zero reads on this setup are inconclusive
and must not be interpreted as panel state. Physical camera checks remain
necessary for scanout verification.

## ChatGPT Dot integration

The existing bridge owns the device connection, persistent queue, and answers.
The MCP adapter is `scripts/pip_mcp.py`; no model runs in the bridge. All five
semantic tools are available over authenticated, stateless HTTP on
`127.0.0.1:8789/mcp`. `scripts/mcp-stdio.py` forwards stdio to that endpoint for
local Codex and Secure MCP Tunnel. The local administrative API remains on
8788; the tunnel must target the MCP adapter, not the administrative API.

| Tool | Arguments | Result |
| --- | --- | --- |
| `send_message` | `request_id`, `text`, optional `importance` | Queue a notice |
| `ask_question` | `request_id`, `text`, `options`, optional `importance` | Queue a decision |
| `get_request` | `request_id` | Text, status, options, selected option |
| `get_device_status` | none | Connection, queue, subscription/delivery health |
| `cancel_request` | `request_id` | Cancel pending request; answered requests retain their answer |

Use caller-generated request IDs (up to 64 ASCII characters); retrying an ID
requires identical content. Text supports printable ASCII/newlines, up to 600
characters. Decisions have one to three `{id, label}` options; labels are at
most 64 characters. `importance` is `normal` or `urgent`. Urgent requests move
ahead of waiting normal requests but never preempt the current screen. Pending
means queued, not confirmed displayed or read. Decisions use the approved
paginated choice-and-confirm flow. Rescan the cloud plugin after updating its
tool schemas or imported skill so the new option-label limit is discovered.

Cancellation persists before notifying the device. A `sync` device message
contains the host's current `card_id` or null; firmware clears a mismatching
card and pending reply. Reconnecting receives `sync` before the current card,
so a cancellation made while offline also clears the stale display. The new
firmware is required for cancellation (an older build ignores `sync`).

### Replies and subscriptions

The MCP Events catalog advertises `device.reply`. Subscribe before sending a
question, optionally filtering by `request_id`. Its data contains `request_id`,
`kind`, `option_id`, and `option_label`. Notice dismissal is acknowledgement,
not approval. The skill tells Dot to recover the request and original task
context before acting. Events contain data, not new instructions.

The server implements `server/discover`, `events/list`, `events/subscribe`, and
`events/unsubscribe` for protocol `2026-07-28`, plus legacy MCP initialization
for local clients. Subscriptions live seven days by default; `ttlMs` grants
between one minute and 30 days. A null requested lifetime receives seven days.
The server returns `refreshBefore` and stops delivery at expiration. Event
replay is not advertised (`cursor: null`); `get_request` recovers missed replies.

Subscription verification and delivery use Standard Webhooks HMAC-SHA256.
Callback URLs must use public HTTPS on port 443. DNS is checked on every
connection, the socket is pinned to a checked address, TLS verifies the
original hostname, and redirects/proxy environment variables are not followed.
Subscriptions, keys, cursors, and the delivery outbox persist in the bridge's
private state file. Retried deliveries preserve event IDs and use fresh
signatures. Transient failures back off, with at most ten attempts; 410 stops a
subscription, and 413/other permanent client errors stop that delivery. A 2xx
means webhook receipt, not completion of the Dot's subsequent action. Consumers
must deduplicate event IDs. Existing bridge answers remain queryable even if
webhook delivery fails. Changing the MCP bearer credential revokes existing
subscriptions after a bridge restart.

Only acknowledged card replies generate MCP events. Listening taps remain
best-effort device diagnostics; there is no microphone audio or transcript yet.

### Setup: device, local bridge, and ChatGPT Dot

This walkthrough targets macOS. Replace the following placeholders with your
own values. No account IDs or API keys belong in this README or in chat.

| Placeholder | Meaning |
| --- | --- |
| `<PROJECT_DIR>` | Absolute path to this repository |
| `<MAC_HOSTNAME>` | Output of `scutil --get LocalHostName`, without `.local` |
| `<TUNNEL_NAME>` | A name you choose for the tunnel, such as `desk-display` |
| `<TUNNEL_ID>` | The `tunnel_…` ID returned by Platform |
| `<WORKSPACE>` | The ChatGPT workspace containing your Dot |
| `<PLUGIN_NAME>` | A name you choose for the ChatGPT plugin |
| `<DOT_NAME>` | Your ChatGPT Dot's name |
| `<RUNTIME_API_KEY>` | A runtime API key with Tunnels Read + Use |

Names such as `rsms-dot`, `pip`, and `local.rsms.pip-bridge` in source paths,
commands, and service labels are fixed identifiers in this implementation,
not example account names. The tunnel helper uses the local alias `rsms-dot`
regardless of the remote `<TUNNEL_NAME>`.

#### 1. Build and flash the device

Install Cargo, uv, Node/npm, and Espressif's Xtensa Rust toolchain using espup
(the pinned toolchain is listed under Build layout). Connect the board over USB.

```sh
cd "<PROJECT_DIR>"
sh scripts/bootstrap.sh
sh scripts/build.sh --locked
.tools/python/bin/python scripts/device.py info
.tools/python/bin/python scripts/device.py flash
```

Keep the checkout at this path after installing services, which use absolute
paths. Reinstall them if the checkout moves. Skip rebuilding/flashing if the
current firmware is already installed.

#### 2. Start the bridge and configure Wi-Fi

Install the bridge as a user launchd service. It runs independently of the
terminal or chat and starts at login. Stop any manually started bridge first;
only one process can own ports 8787, 8788, and 8789.

```sh
.tools/python/bin/python scripts/bridge-service.py install
scutil --get LocalHostName
.tools/python/bin/python scripts/device.py configure --host "<MAC_HOSTNAME>.local"
curl --fail http://127.0.0.1:8788/health
```

The configuration command prompts locally for the 2.4 GHz Wi-Fi network and
hidden password, and sends them over USB. This launchd service uses TCP, so
omit `--via-bridge`; that flag is only for a bridge started with `--serial`.
Wait for health to show `connected: true` and `transport: "tcp"`.
The Mac and device must be on a LAN that permits their connection and mDNS.
The Mac must remain awake and online for the physical interface to work.

#### 3. Install the local skill and MCP connection

This step gives local Codex access. Cloud Dot access is configured separately
in steps 4–6.

```sh
mkdir -p "$HOME/.agents/skills"
ln -s "$PWD/skills/rsms-dot" "$HOME/.agents/skills/rsms-dot"
codex mcp add rsms-dot -- "$PWD/.tools/python/bin/python" "$PWD/scripts/mcp-stdio.py"
```

If the symlink or MCP entry already exists, inspect it instead of creating a
second copy. The stdio adapter reads the local bearer credential itself; no
API key or environment variable is needed in this MCP registration.

For manual entry in a desktop **local MCP** form, choose **STDIO**:

| Field | Value |
| --- | --- |
| Name | `rsms-dot` |
| Command | `<PROJECT_DIR>/.tools/python/bin/python` |
| Argument | `<PROJECT_DIR>/scripts/mcp-stdio.py` |
| Working directory | `<PROJECT_DIR>` |
| Environment variables | None |

That desktop form's **STDIO / Streamable HTTP** selector configures a local
connection. It is not the cloud tunnel form used in step 5.

#### 4. Create and start a Secure MCP Tunnel

Install the official client; the helper verifies its release SHA-256 checksum:

```sh
.tools/python/bin/python scripts/tunnel.py install-client
```

In [Platform tunnel settings](https://platform.openai.com/settings/organization/tunnels),
create `<TUNNEL_NAME>` and associate it with `<WORKSPACE>`. Copy `<TUNNEL_ID>`.
Creating a tunnel requires Tunnels Read + Manage. Create a separate runtime
API key whose principal has Tunnels Read + Use; do not use an admin key for the
running daemon.

```sh
.tools/python/bin/python scripts/tunnel.py configure
```

Enter `<TUNNEL_ID>` at the first prompt and `<RUNTIME_API_KEY>` at the hidden
key prompt. The helper stores the key in `.tools/tunnel-runtime-key`, mode
0600, and starts a managed tunnel runtime using a file reference to that key.
The tunnel forwards to the stdio adapter. It does not require router port
forwarding or a public listener on the Mac.

```sh
.tools/python/bin/python scripts/tunnel.py status
.tools/python/bin/python scripts/tunnel.py doctor
```

Expect `process_running`, `healthy`, and `ready` to be true. A running tunnel
alone does not establish a ChatGPT plugin or event subscription.

#### 5. Connect the cloud plugin in a web browser

Open [ChatGPT Plugins](https://chatgpt.com/plugins) **in a web browser**, signed
into `<WORKSPACE>`. This is distinct from the desktop local MCP form.

1. Choose **Add custom MCP server**.
2. Name it `<PLUGIN_NAME>`.
3. Under **Connection**, choose **Tunnel** and select `<TUNNEL_NAME>` or enter
   `<TUNNEL_ID>`.
4. Under **Authentication**, choose **No authentication**. The tunnel is
   authenticated using its runtime credential and Platform/workspace access
   controls. Our adapter supplies the separate local bridge credential;
   this server does not implement an additional OAuth login.
5. Create/connect the private plugin and scan its capabilities. Expect
   `send_message`, `ask_question`, `get_request`, `get_device_status`,
   `cancel_request`, and the `device.reply` event.
6. Make the plugin available to `<DOT_NAME>` through its connected apps/plugins.

If Tunnel is unavailable, check the selected workspace, its association with
`<TUNNEL_ID>`, and your tunnel permissions. If you only see **STDIO** and
**Streamable HTTP**, check that you are using the web cloud connection form.

The server also exposes the `io.modelcontextprotocol/skills` extension,
`skills/list`, `skills/get`, and `resources/read`, with a SHA-256 digest for
`skills/rsms-dot/SKILL.md`. Skill import is a scan-time snapshot; rescan after
updating the skill. The local symlink alone does not install a cloud skill.
No public plugin-directory publication is needed for this private setup.

#### 6. Verify a complete Dot interaction

Send this in `<DOT_NAME>`'s own chat, replacing `<PLUGIN_NAME>`:

> Use `<PLUGIN_NAME>`. Subscribe to device.reply for request_id
> desk-display-test-001. Ask "Can you see this?" on my desk display with options
> Yes and No, using that request ID. When the reply event arrives, tell me here
> which option I chose.

Tap an option on the device. Confirm that the Dot reports that choice in the
same conversation. This checks tool delivery, the physical reply, signed
webhook receipt, and actual cloud continuation. Sending a question without a
subscription does not arrange a wakeup. Use a fresh request ID for a new test;
reuse an ID only to retry the same request.

`get_device_status` reports active reply subscriptions and webhook delivery
counts. `get_request` recovers a saved answer. A webhook's successful receipt
is not proof that the Dot has completed its subsequent action.

For latency diagnosis, private bridge state records the card's `created_at`
and `answered_at`, and each webhook's `first_attempt_at`, `last_attempt_at`,
`last_completed_at`, and `last_duration_ms`. These distinguish local dispatch
and HTTP delivery from the time the cloud Dot takes to continue. Timing fields
are recorded for new delivery attempts; older deliveries may lack them.

#### 7. Restart, diagnose, and package

```sh
# After editing bridge/MCP code:
.tools/python/bin/python scripts/bridge-service.py restart
.tools/python/bin/python scripts/bridge-service.py status

# Reconnect a saved tunnel configuration after stopping/rebooting:
.tools/python/bin/python scripts/tunnel.py start
.tools/python/bin/python scripts/tunnel.py status
.tools/python/bin/python scripts/tunnel.py doctor
```

Bridge logs are `.tools/bridge.log` and `.tools/bridge-error.log`. The launchd
plist is `~/Library/LaunchAgents/local.rsms.pip-bridge.plist`. Subscription
state and answers survive bridge restarts; the device reconnects automatically.
Keep the bridge and managed tunnel runtime running while the plugin is in use.

Private files under ignored `.tools/` include `bridge-config.json` (device
pairing token), `bridge-state.json` (cards, replies, webhook subscriptions and
signing keys), `mcp-config.json` (local MCP bearer token), and
`tunnel-runtime-key` (OpenAI runtime credential). Keep these local; do not add
them to Git, plugin archives, screenshots, or documentation.

`plugins/rsms-dot/plugin.json` is the portable manifest. Run
`python3 scripts/package-plugin.py` to build `.tools/rsms-dot-plugin/` and its
ZIP, containing a real copy of the skill and this host's stdio config. That
package is for local installation; cloud uses the tunnel above. The generated
archive excludes credentials, state, and runtime profiles.

### Integration validation

```sh
.tools/python/bin/python -m unittest discover -s scripts -p 'test_*.py'
# Optional isolated official SDK interoperability test (mcp 2.3.0 tested):
uv venv --python 3.11 .tools/mcp-test-python
uv pip install --python .tools/mcp-test-python/bin/python mcp==2.3.0
.tools/mcp-test-python/bin/python scripts/mcp-client-smoke.py
# Real hardware; requires an empty queue. Creates only its own test requests.
.tools/python/bin/python scripts/mcp-device-smoke.py
```

Host tests cover durable replies and webhook retries across restarts, filtering,
verification failures, signing, credential rotation, idempotency, cancellation,
priority ordering, HTTP authentication, and blocked callback destinations.
All 18 host tests pass. The official SDK test passes modern and legacy
discovery, tools, and resources over both stdio and HTTP.
The hardware test passed on the device after one transient USB inspection
timeout. It sends requests through MCP/TCP, cancels a question, navigates
and dismisses a message using simulated touch, then reads its persisted reply
through MCP. Captures go in `.tools/mcp-smoke/`. Simulated touch is not evidence
of a human tap or a successful cloud Dot subscription.

A separate live cloud test also passed: the Dot subscribed, sent a question,
the user tapped Yes on the device, the webhook received HTTP 200 on its first
attempt, and the Dot reported Yes in the original conversation. End-to-end
interaction was slow; webhook dispatch began about 144 ms after the bridge
received the tap, but HTTP completion timing was not yet recorded. Questions
used the prototype option buttons during that test; the later reply-choice
design replaces them.

References: [MCP Events](https://developers.openai.com/plugins/build/mcp-events),
[Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels),
[MCP skill import](https://developers.openai.com/plugins/build/mcp-server), and
[local skill discovery](https://learn.chatgpt.com/docs/build-skills).


## Procedural sleep animation

The sleeping scene is generated in `components/pip_board/pip_sleep.c`; no blob
sprites or frame sequence are stored. An antialiased capsule and two closed-eye
arcs are evaluated in rotating coordinates into a 128 × 104 RGB565 tile. The
capsule stays on the bottom baseline while changing shape and rolling. The zZ
strokes are generated once into two small tiles with staggered fades.

The cycle is 1 second still, 4 seconds breathing, 1 second still, and 3 seconds
of fading zZ. Each completed cycle has a 1-in-10 chance of a 3-second tumble to
a different bottom position. The blob stays within the design's side margins.
Taps and incoming cards stop the animation immediately. Still phases avoid
redrawing the blob; animation positions use elapsed time rather than frame count.

```sh
# Capture reference poses, measure 12 seconds of continuous tumbling, restore normal sleep.
.tools/python/bin/python scripts/sleep-bench.py
# Leave the benchmark tumbling for visual inspection.
.tools/python/bin/python scripts/sleep-bench.py --leave-running
```

USB debug commands (while sleeping):

```json
{"type":"sleep_animation","repeat":true}
{"type":"sleep_animation","repeat":true,"seek_ms":750}
{"type":"sleep_animation","repeat":false,"seek_ms":7500}
{"type":"sleep_animation","repeat":false}
```

`seek_ms` freezes a pose for pixel inspection; omitting it resumes time. `inspect`
includes phase, elapsed milliseconds, position, draw count, and rasterization
time. This benchmark measured 28.6 nonempty LVGL refreshes/second, about 5.8 ms
median refresh work, and approximately 7.5 ms procedural rasterization per
moving frame. This measures submitted refreshes, not panel-synchronized scanout;
TE/vsync synchronization and full-screen animated transitions remain future work.
