# Hardware and porting

[Back to the project guide](../README.md).

The current board support targets the Waveshare ESP32-S3-Touch-AMOLED-1.8.
Application behavior and the host protocol can be reused, but a different board
needs drivers and configuration for its peripherals. Xtensa and FreeRTOS alone
do not define display, touch, audio, or power interfaces.

## Reference board

| Component | Configuration |
| --- | --- |
| MCU | ESP32-S3, dual Xtensa LX7, 240 MHz |
| Memory | 512 KiB internal SRAM, 8 MiB octal PSRAM at 80 MHz, 16 MiB flash |
| Display | 368 × 448 QSPI AMOLED; logical landscape canvas 448 × 368 |
| V1 display / touch | SH8601 / FT3168; no horizontal gap |
| V2 display / touch | CO5300 / CST820; 16-pixel horizontal gap |
| Power / audio | AXP2101 PMIC / ES8311 codec |
| Other peripherals | PCF85063 RTC, QMI8658 IMU, microSD, Wi-Fi/BLE |
| USB | Native USB Serial/JTAG, VID:PID 303a:1001 |

Touch probing selects the display revision before panel creation. Failure to
identify either controller is an error. Compatible driver-family names in the
BSP may differ from the fitted controller name. V2 is the tested hardware path;
V1 support follows the vendor BSP and needs separate physical verification.

| Signal | GPIO |
| --- | --- |
| Display CLK / CS | 11 / 12 |
| Display D0 / D1 / D2 / D3 | 4 / 5 / 6 / 7 |
| Shared I2C SDA / SCL | 15 / 14 |
| Touch interrupt | 21 |
| USB D− / D+ | 19 / 20 |
| microSD CMD / CLK / D0 | 1 / 2 / 3 |
| Audio SCLK / MCLK / LRCLK | 9 / 16 / 45 |
| Audio DOUT / DIN / amplifier enable | 8 / 10 / 46 |

## Display and memory

The enclosure orientation uses LVGL software rotation (`LV_DISPLAY_ROTATION_90`).
Touch coordinates stay in native panel space; LVGL applies the inverse transform.
Rotating them again in the driver breaks input alignment.

The V2 startup override follows the Waveshare CO5300 reset/wake sequence: reset,
wait 200 ms, sleep-out, wait 120 ms, then configure pixel format and controls,
turn on the display, set brightness, and wait 10 ms. Shorter reset delays or
programming before wake can leave the physical screen blank even when LVGL
screenshots look correct. Test cold power-on as well as reset when changing it.
Do not change PMIC rail voltages without checking the schematic.

RGB565 uses 329,728 bytes per full frame. Two 32-row DMA buffers plus software
rotation scratch use about 69 KiB of internal memory. Screenshot storage uses
one full frame in PSRAM and another temporarily during capture. Keep DMA buffers
and interrupt-critical allocations in internal memory. LVGL uses the ESP-IDF
allocator so larger allocations can use PSRAM.

The display port transfers dirty regions over 40 MHz QSPI. Regions need even
coordinates and pixel counts. Small animated regions are cheaper than full-screen
redraws; refresh statistics measure rendering/transfer work, not tear-free panel
scanout. TE/vsync synchronization is not implemented.

Brightness is a panel command, not PWM. `CONFIGURED_BRIGHTNESS` and
`SLEEP_BRIGHTNESS` in `pip_board.c` set the active/sleep levels. Sleep is an
animated UI state, not MCU deep sleep.

The screenshot path copies flushed RGB565 pixels before rotation and byte swap.
USB transfers include dimensions and CRC-32 checks. It is not panel-memory
readback. Use bulk USB writes and yield during long transfers to avoid dropped
console output or watchdog starvation.

## Audio and flash

The codec captures 16 kHz mono PCM16 with 30 dB input gain. Firmware drains
300 ms of startup samples and applies a 5 ms fade before streaming. The host
buffers at most 30 seconds and gates short/quiet recordings before recognition.
Audio has its own TCP connection so it does not block card traffic.

`partitions.csv` reserves a 4 MiB factory application at 0x10000 and keeps the
NVS/PHY partitions at 0x9000/0xf000. `device.py flash` generates the actual
partition table and checks image size; use it rather than assuming the temporary
ESP-IDF build project's default table is correct. Keep USB GPIO19/20 available
for ROM-loader recovery.

## Adapting another board

Start with `components/pip_board/include/pip_board.h`, the Rust/C boundary.
Replace the BSP dependency and board initialization, then adapt display dimensions,
rotation, touch transforms, microphone setup, and Wi-Fi provisioning as needed.
Update the Rust target, SDK configuration, and flash layout for the new MCU and
memory. Audit UI coordinates and asset scaling if the display size changes.

Bring up display and touch before network/audio, then verify message pagination,
choice confirmation, reconnect/reply handling, recording, and cold power-on.
The C bridge uses C17 with GNU extensions required by ESP-IDF; Rust executes
native machine code with ESP-IDF's FreeRTOS scheduler linked into the firmware.

## Vendor references

- [Waveshare hardware documentation](https://docs.waveshare.com/ESP32-S3-Touch-AMOLED-1.8)
- [Vendor examples at the reference revision](https://github.com/waveshareteam/ESP32-S3-Touch-AMOLED-1.8/tree/78e13f852929c2ab4f9d5e0ad1c50ea378dbf2b4)
- [Waveshare BSP 2.0.3](https://components.espressif.com/components/waveshare/esp32_s3_touch_amoled_1_8/versions/2.0.3)
