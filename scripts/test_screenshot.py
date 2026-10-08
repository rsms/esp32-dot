import io
import struct
import unittest
import zlib

from screenshot import encode_png, read_capture


class ScreenshotTests(unittest.TestCase):
    pixels = struct.pack("<4H", 0xf800, 0x07e0, 0x001f, 0xffff)

    def protocol(self, pixels=None, offset=0):
        data = self.pixels if pixels is None else pixels
        return (f"log before header\nPIPSHOT 1 2 2 RGB565LE 8 {zlib.crc32(self.pixels):08x} 3 1000\r\n"
            f"PIPDATA {offset} {data.hex()}\r\nlog between lines\nPIPEND\n").encode()

    def test_valid_capture_with_console_logs(self):
        metadata, pixels = read_capture(io.BytesIO(self.protocol()))
        self.assertEqual(metadata["width"], 2)
        self.assertEqual(pixels, self.pixels)

    def test_rejects_corruption_truncation_and_missing_chunk(self):
        for protocol in (self.protocol(b"\x00" * 8), self.protocol(b"\x00"), self.protocol(offset=128)):
            with self.subTest(protocol=protocol), self.assertRaises(ValueError):
                read_capture(io.BytesIO(protocol))

    def test_png_color_channels_and_rows(self):
        png = encode_png(2, 2, self.pixels)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        pos = 8
        compressed = bytearray()
        while pos < len(png):
            size = struct.unpack_from(">I", png, pos)[0]
            kind, data = png[pos + 4:pos + 8], png[pos + 8:pos + 8 + size]
            crc = struct.unpack_from(">I", png, pos + 8 + size)[0]
            self.assertEqual(crc, zlib.crc32(kind + data))
            if kind == b"IDAT":
                compressed.extend(data)
            pos += size + 12
        self.assertEqual(zlib.decompress(compressed),
            bytes([0, 255, 0, 0, 0, 255, 0, 0, 0, 0, 255, 255, 255, 255]))


if __name__ == "__main__":
    unittest.main()
