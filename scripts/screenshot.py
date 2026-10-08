"""Read pip's loss-checked display capture and encode PNG without extra packages."""
import binascii
from pathlib import Path
import struct
import time
import zlib


def read_capture(stream, timeout=30):
    deadline = time.monotonic() + timeout
    pending = bytearray()
    pixels = bytearray()
    metadata = None
    while time.monotonic() < deadline:
        pending.extend(stream.read(4096))
        while b"\n" in pending:
            line, _, pending = pending.partition(b"\n")
            fields = line.split()
            if not fields:
                continue
            if fields[0] == b"PIPERROR":
                raise RuntimeError(line.decode("ascii", errors="replace"))
            if fields[0] == b"PIPSHOT":
                if len(fields) != 9 or fields[1] != b"1" or fields[4] != b"RGB565LE":
                    raise ValueError("Unsupported screenshot header")
                width, height, size = int(fields[2]), int(fields[3]), int(fields[5])
                if not (0 < width <= 1024 and 0 < height <= 1024 and size == width * height * 2):
                    raise ValueError("Invalid screenshot dimensions")
                metadata = dict(width=width, height=height, size=size,
                    crc32=int(fields[6], 16), flushes=int(fields[7]), uptime_ms=int(fields[8]))
                pixels.clear()
            elif fields[0] == b"PIPDATA" and metadata:
                if len(fields) != 3 or int(fields[1]) != len(pixels):
                    raise ValueError(f"Missing or out-of-order screenshot data at byte {len(pixels)}")
                pixels.extend(binascii.unhexlify(fields[2]))
                if len(pixels) > metadata["size"]:
                    raise ValueError("Screenshot exceeds announced length")
            elif fields[0] == b"PIPEND" and metadata:
                if len(pixels) != metadata["size"] or zlib.crc32(pixels) != metadata["crc32"]:
                    raise ValueError("Incomplete screenshot or CRC mismatch")
                if metadata["flushes"] == 0:
                    raise ValueError("The display has not submitted any pixels yet")
                return metadata, pixels
        if len(pending) > 4096:
            raise ValueError("Oversized screenshot protocol line")
    raise TimeoutError("Timed out waiting for screenshot; flash firmware with screenshot support")


def encode_png(width, height, pixels):
    if len(pixels) != width * height * 2:
        raise ValueError("RGB565 size does not match dimensions")
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # PNG filter: none.
        for x in range(width):
            value = struct.unpack_from("<H", pixels, 2 * (y * width + x))[0]
            r, g, b = value >> 11, (value >> 5) & 63, value & 31
            rows.extend(((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def save_capture(stream, output, timeout=30):
    stream.reset_input_buffer()
    stream.write(b"screenshot\n")
    stream.flush()
    metadata, pixels = read_capture(stream, timeout)
    path = Path(output).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_png(metadata["width"], metadata["height"], pixels))
    print(f"{path} ({metadata['width']}×{metadata['height']}, "
        f"{metadata['flushes']} flushes, device uptime {metadata['uptime_ms']} ms, CRC verified)")
