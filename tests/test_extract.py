import hashlib
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from razer_updater_extract.extract import FormatError, decode_hex, extract


def record(address, kind, data=b""):
    body = bytes([len(data)]) + address.to_bytes(2, "big") + bytes([kind]) + data
    return ":" + (body + bytes([-sum(body) % 256])).hex().upper()


def resources(entries):
    """Small .NET v2 resource file using only strings and Int32 values."""
    def string(data):
        return bytes([len(data)]) + data

    names, values, offsets = bytearray(), bytearray(), []
    for name, value in entries.items():
        offsets.append(len(names))
        names.extend(string(name.encode("utf-16le")))
        names.extend(struct.pack("<I", len(values)))
        if isinstance(value, str):
            values.extend(b"\x01" + string(value.encode("utf-8")))
        else:
            values.extend(b"\x08" + struct.pack("<i", value))
    header = struct.pack("<6I", 0xBEEFCACE, 1, 0, 2, len(entries), 0)
    header += bytes(4 * len(entries))
    header += struct.pack(f"<{len(entries)}I", *offsets)
    return header + struct.pack("<I", len(header) + 4 + len(names)) + names + values


class ExtractionTests(unittest.TestCase):
    def test_sparse_segments_keep_addresses(self):
        segments, entry = decode_hex([
            record(0, 4, b"\x20\x00"), record(4, 0, b"abc"),
            record(16, 0, b"d"), record(0, 5, b"\x20\x00\x00\x05"), record(0, 1),
        ])
        self.assertEqual(segments, [(0x20000004, b"abc"), (0x20000010, b"d")])
        self.assertEqual(entry, 0x20000005)

    def test_reject_invalid_hex(self):
        data = record(0, 0, b"a")
        for lines in ([data], [data, data, record(0, 1)],
                      [data[:-2] + "00", record(0, 1)], [record(0, 1), data]):
            with self.subTest(lines=lines), self.assertRaises(FormatError):
                decode_hex(lines)

    def test_extract_two_streams_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            updater, output = root / "updater.exe", root / "firmware"
            updater.write_bytes(b"MZ")
            entries = {}
            for number, value in ((1, b"a"), (2, b"b")):
                stem = f"Dev{number}FWLine"
                entries[stem + "0"] = record(number, 0, value)
                entries[stem + "1"] = record(0, 1)
                entries[stem + "Num"] = 2
            with zipfile.ZipFile(updater, "a") as archive:
                archive.writestr("DeviceUpdater.resources", resources(entries))
            manifest = extract(updater, output)
            self.assertEqual(len(manifest["payloads"]), 2)
            self.assertEqual(
                {path.name for path in output.iterdir()},
                {"dev1fw.bin", "dev2fw.bin", "manifest.json"},
            )
            self.assertEqual((output / "dev1fw.bin").read_bytes(), b"a")
            self.assertEqual((output / "dev2fw.bin").read_bytes(), b"b")
            self.assertEqual(
                manifest["payloads"][1]["segments"][0]["sha256"],
                hashlib.sha256(b"b").hexdigest(),
            )
            with self.assertRaises(FileExistsError):
                extract(updater, output)
