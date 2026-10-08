#!/usr/bin/env python3
"""Identify, flash, or monitor the one connected native ESP32 USB interface."""
import argparse
import getpass
import json
from pathlib import Path
import subprocess
import struct
import sys
import time
import urllib.request

import serial
from serial.tools import list_ports
from esptool.reset import HardReset
from screenshot import save_capture

ROOT = Path(__file__).resolve().parent.parent


def run(*args):
    subprocess.run([sys.executable, "-m", "esptool", "--chip", "esp32s3", *map(str, args)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["info", "flash", "monitor", "screenshot", "configure", "set-host"])
    parser.add_argument("--port")
    parser.add_argument("--seconds", type=float, default=15)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--output", default=".tools/screenshot.png")
    parser.add_argument("--host", help="Mac's hostname (e.g. rmbm5.local) or LAN address")
    parser.add_argument("--ssid", help="2.4 GHz Wi-Fi network name")
    parser.add_argument("--tcp-port", type=int, default=8787)
    parser.add_argument("--via-bridge", action="store_true", help="Provision through the bridge that currently owns USB")
    args = parser.parse_args()
    candidates = [p.device for p in list_ports.comports() if p.vid == 0x303A and p.pid == 0x1001]
    port = args.port
    if not port:
        if len(candidates) != 1:
            parser.error(f"Expected one ESP32 USB device, found {candidates}; specify --port")
        port = candidates[0]

    configuration = None
    if args.action == "set-host":
        if not args.host:
            parser.error("set-host requires --host")
        configuration = dict(type="set_host", host=args.host, port=args.tcp_port)
    if args.action == "configure":
        if not args.host:
            parser.error("configure requires --host with this Mac's LAN address")
        config_path = ROOT / ".tools/bridge-config.json"
        if not config_path.exists():
            parser.error("Run scripts/bridge.py once to create the bridge token")
        ssid = args.ssid or input("Wi-Fi network (2.4 GHz): ")
        password = getpass.getpass("Wi-Fi password (empty for an open network): ")
        configuration = dict(type="configure", ssid=ssid, password=password,
            host=args.host, port=args.tcp_port, token=json.loads(config_path.read_text())["token"])
        if args.via_bridge:
            request = urllib.request.Request("http://127.0.0.1:8788/configure",
                json.dumps(configuration).encode(), {"Content-Type": "application/json"})
            result = json.load(urllib.request.urlopen(request, timeout=20))
            if not result.get("ok"):
                raise RuntimeError("Device rejected network configuration")
            print("Wi-Fi settings saved on pip; it will connect to the host bridge.")
            return

    if args.action == "info":
        run("--port", port, "flash-id")
        run("--port", port, "get-security-info")
    elif args.action == "flash":
        release = ROOT / "target/xtensa-esp32s3-espidf/release"
        builds = list(release.glob("build/esp-idf-sys-*/out/build/bootloader/bootloader.bin"))
        if len(builds) != 1:
            parser.error("Expected one release build; clean stale build outputs and rebuild")
        bootloader = builds[0]
        build = bootloader.parent.parent
        # esp-idf-sys builds its own temporary CMake project. Generate the real
        # device table here, as recommended for Rust custom partitions.
        partition = ROOT / ".tools/partitions.bin"
        generator = ROOT / ".embuild/espressif/esp-idf/v5.5.5/components/partition_table/gen_esp32part.py"
        subprocess.run([sys.executable, str(generator), str(ROOT / "partitions.csv"),
            str(partition)], check=True)
        settings = json.loads((build / "flasher_args.json").read_text())["flash_settings"]
        firmware = release / "pip-firmware.bin"
        flash_flags = ["--flash-mode", settings["flash_mode"],
            "--flash-size", settings["flash_size"], "--flash-freq", settings["flash_freq"]]
        run("elf2image", *flash_flags, "--output", firmware, release / "pip-firmware")
        entries = partition.read_bytes()
        factory = None
        for pos in range(0, len(entries) - 31, 32):
            magic, kind, subtype, offset, size = struct.unpack_from("<HBBII", entries, pos)
            if magic == 0x50AA and kind == 0 and subtype == 0:
                factory = (offset, size)
                break
        if factory is None:
            parser.error("Partition table has no factory application partition")
        if firmware.stat().st_size > factory[1]:
            parser.error("Firmware exceeds the configured factory application partition")
        run("--port", port, "write-flash", *flash_flags,
            "0x0", bootloader, "0x8000", partition, hex(factory[0]), firmware)
    else:
        # On native USB, these flags suppress PySerial's DTR/RTS writes at
        # open, which otherwise reset the S3 on macOS. No physical CTS/DSR
        # pins are involved. Capture/monitor must preserve the running UI.
        stream = serial.Serial(baudrate=115200, timeout=0.2, dsrdtr=True, rtscts=True)
        stream.port = port
        with stream:
            if args.reset:
                # DTR must be deasserted while pulsing RTS for an app reset.
                stream.dtr = False
                stream.rts = False
                HardReset(stream, uses_usb=True)()
            if configuration:
                stream.reset_input_buffer()
                stream.write(json.dumps(configuration, separators=(",", ":")).encode() + b"\n")
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    line = stream.readline()
                    if line.startswith(b"PIPEVENT "):
                        event = json.loads(line[9:])
                        if event.get("type") == "configured":
                            if not event.get("ok"):
                                raise RuntimeError("Device rejected network configuration")
                            print("Host endpoint saved on pip." if args.action == "set-host" else
                                "Wi-Fi settings saved on pip; it will connect to the host bridge.")
                            return
                raise TimeoutError("No provisioning acknowledgement from pip")
            if args.action == "screenshot":
                if args.reset:
                    time.sleep(2)
                save_capture(stream, args.output, max(30, args.seconds))
                return
            deadline = time.monotonic() + args.seconds
            while time.monotonic() < deadline:
                data = stream.read(4096)
                if data:
                    sys.stdout.write(data.decode("utf-8", errors="replace"))
                    sys.stdout.flush()


if __name__ == "__main__":
    main()
