"""Read firmware payloads from Razer updater resources without running the updater."""

import hashlib
import json
import re
import struct
import zipfile
from pathlib import Path

MAX_UPDATER = 128 * 1024 * 1024
MAX_RESOURCE = 32 * 1024 * 1024
STREAM = re.compile(r"(Dev(?:[0-9]+)?FWLine)([0-9]+)")
RESOURCE = "DeviceUpdater.resources"


class FormatError(ValueError):
    """An unsupported or malformed updater."""


class Reader:
    def __init__(self, data, position=0, end=None):
        self.data = data
        self.position = position
        self.end = len(data) if end is None else end

    def read(self, length):
        end = self.position + length
        if self.position < 0 or end > self.end:
            raise FormatError("Truncated .NET resource")
        value = self.data[self.position:end]
        self.position = end
        return value

    def u32(self):
        return struct.unpack("<I", self.read(4))[0]

    def varint(self):
        result = 0
        for shift in range(0, 35, 7):
            value = self.read(1)[0]
            result |= (value & 127) << shift
            if value < 128 and result <= 0x7FFFFFFF:
                return result
        raise FormatError("Invalid .NET resource length")

    def string(self, encoding="utf-8"):
        return self.read(self.varint()).decode(encoding)


def read_resources(data):
    """Decode built-in strings and integers. Custom .NET objects are never deserialized."""
    header = Reader(data)
    if (header.u32(), header.u32()) != (0xBEEFCACE, 1):
        raise FormatError("Unsupported .NET resource header")
    header.read(header.u32())
    if header.u32() != 2:
        raise FormatError("Expected .NET resource format version 2")
    count, type_count = header.u32(), header.u32()
    if count > len(data) // 8 or type_count > len(data):
        raise FormatError("Invalid .NET resource table size")
    for _ in range(type_count):
        header.string()
    header.read(-header.position % 8)
    header.read(count * 4)  # Name hashes.
    positions = [header.u32() for _ in range(count)]
    values_start = header.u32()
    names_start = header.position
    if not names_start <= values_start <= len(data):
        raise FormatError("Invalid .NET resource data offset")

    result = {}
    for offset in positions:
        name_reader = Reader(data, names_start + offset, values_start)
        name = name_reader.string("utf-16le")
        value_reader = Reader(data, values_start + name_reader.u32())
        if name in result:
            raise FormatError(f"Duplicate .NET resource: {name}")
        kind = value_reader.varint()
        if kind == 1:
            result[name] = value_reader.string()
        elif kind == 8:
            result[name] = struct.unpack("<i", value_reader.read(4))[0]
        else:
            result[name] = None
    return result


def decode_hex(lines):
    """Return ordered contiguous segments and any execution entry point."""
    records = []
    upper, entry, eof = 0, None, False
    for number, line in enumerate(lines, 1):
        if eof or not isinstance(line, str) or not re.fullmatch(r":[0-9a-fA-F]+", line):
            raise FormatError(f"Invalid Intel HEX record at line {number}")
        try:
            record = bytes.fromhex(line[1:])
        except ValueError as exc:
            raise FormatError(f"Invalid Intel HEX digits at line {number}") from exc
        if len(record) < 5 or len(record) != record[0] + 5 or sum(record) % 256:
            raise FormatError(f"Invalid Intel HEX length or checksum at line {number}")
        address = int.from_bytes(record[1:3], "big")
        kind, data = record[3], record[4:-1]
        if kind == 0:
            if not data or address + len(data) > 0x10000 or upper + address + len(data) > 2**32:
                raise FormatError(f"Invalid data address at Intel HEX line {number}")
            records.append((upper + address, data))
            continue
        sizes = {1: 0, 2: 2, 3: 4, 4: 2, 5: 4}
        if address or sizes.get(kind) != len(data):
            raise FormatError(f"Unsupported or malformed Intel HEX record at line {number}")
        if kind == 1:
            eof = True
        elif kind in (2, 4):
            upper = int.from_bytes(data, "big") << (4 if kind == 2 else 16)
        else:
            if entry is not None:
                raise FormatError("Multiple Intel HEX entry point records")
            entry = int.from_bytes(data, "big")
            if kind == 3:
                entry = (int.from_bytes(data[:2], "big") << 4) + int.from_bytes(data[2:], "big")
    if not eof or not records:
        raise FormatError("Intel HEX payload requires data and an end-of-file record")

    segments = []
    for address, data in sorted(records):
        if segments:
            start, previous = segments[-1]
            end = start + len(previous)
            if address < end:
                raise FormatError("Overlapping Intel HEX data records")
            if address == end:
                previous.extend(data)
                continue
        segments.append((address, bytearray(data)))
    return segments, entry


def extract(updater: Path, output: Path):
    """Extract all validated streams to a new output directory."""
    with updater.open("rb") as source:
        if source.read(2) != b"MZ":
            raise FormatError("Expected a Windows updater executable")
        if source.seek(0, 2) > MAX_UPDATER:
            raise FormatError("Updater exceeds the supported size limit")
        source.seek(0)
        digest = hashlib.file_digest(source, "sha256").hexdigest()
        with zipfile.ZipFile(source) as archive:
            matches = [item for item in archive.infolist() if item.filename == RESOURCE]
            if len(matches) != 1 or matches[0].file_size > MAX_RESOURCE:
                raise FormatError(f"Expected one bounded {RESOURCE} ZIP member")
            resources = read_resources(archive.read(matches[0]))

    streams = {}
    for name, value in resources.items():
        if match := STREAM.fullmatch(name):
            stem, number = match.groups()
            index = int(number)
            records = streams.setdefault(stem, {})
            if index in records:
                raise FormatError(f"Duplicate line index in {stem}")
            records[index] = value
    if not streams:
        raise FormatError("No supported DevFWLine or numbered DevNFWLine streams found")

    files = {}
    manifest = {"updater_sha256": digest, "payloads": []}
    for stem, records in sorted(streams.items()):
        count = resources.get(stem + "Num")
        if type(count) is not int or count != len(records) or sorted(records) != list(range(count)):
            raise FormatError(f"Missing or invalid line indices in {stem}")
        lines = [records[i] for i in range(count)]
        segments, entry = decode_hex(lines)
        name = stem.removesuffix("Line").lower()
        suffix = stem.removeprefix("Dev").removesuffix("FWLine")
        payload = {
            "resource": stem,
            "firmware_version": resources.get("DevFWVer" + suffix),
            "entry_point": entry,
            "segments": [],
        }
        for address, data in segments:
            filename = f"{name}.bin" if len(segments) == 1 else f"{name}-{address:08x}.bin"
            files[filename] = data
            payload["segments"].append({
                "file": filename,
                "address": address,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            })
        manifest["payloads"].append(payload)
    files["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    output.mkdir()
    for name, data in files.items():
        with (output / name).open("xb") as target:
            target.write(data)
    return manifest
