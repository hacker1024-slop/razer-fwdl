"""Command line selection, download and extraction."""

import argparse
import hashlib
import json
import sys
import tempfile
import urllib.request
import zipfile
from importlib.resources import files
from pathlib import Path

from .extract import MAX_UPDATER, FormatError, extract


def registry():
    return json.loads(files(__package__).joinpath("registry.json").read_text())["devices"]


def choose(items, label, describe):
    print(f"Choose {label}:")
    for index, item in enumerate(items, 1):
        print(f"  {index}. {describe(item)}")
    while True:
        answer = input("> ").strip()
        if answer.isascii() and answer.isdecimal() and len(answer) < 10:
            index = int(answer)
            if 1 <= index <= len(items):
                return items[index - 1]
        print("Enter a number from the list.")


def device_label(device):
    return f"{device['name']} ({device['model']}, {device['category']})"


def download(release, target):
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(release["url"], timeout=30) as response, target.open("xb") as out:
        if not response.geturl().startswith("https://"):
            raise ValueError("Updater download redirected away from HTTPS")
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPDATER:
                raise ValueError("Updater download exceeds the supported size limit")
            digest.update(chunk)
            out.write(chunk)
    if digest.hexdigest() != release["sha256"]:
        raise ValueError("Downloaded updater SHA-256 does not match the registry")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Extract firmware from Razer updater executables.")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("list", help="List registered devices and updater versions")
    local = commands.add_parser("extract", help="Extract a local updater executable")
    local.add_argument("updater", type=Path)
    local.add_argument("-o", "--output", type=Path, required=True)
    remote = commands.add_parser("download", help="Download and extract a registered release")
    remote.add_argument("--device", help="Device model from the registry")
    remote.add_argument("--version", help="Updater release from the registry")
    remote.add_argument("-o", "--output", type=Path)
    args = parser.parse_args(argv)

    try:
        if args.command == "extract":
            manifest = extract(args.updater, args.output)
            output = args.output
        else:
            devices = registry()
            if args.command == "list":
                for device in devices:
                    print(f"{device_label(device)}: " + ", ".join(
                        release["version"] for release in device["releases"]
                    ))
                return 0
            model = getattr(args, "device", None)
            version = getattr(args, "version", None)
            if model:
                device = next((d for d in devices if d["model"].casefold() == model.casefold()), None)
                if device is None:
                    raise ValueError(f"Unknown model {model!r}; run list to see available devices")
            else:
                if not sys.stdin.isatty():
                    raise ValueError("Use download --device MODEL --version VERSION or extract FILE")
                device = choose(devices, "a device", device_label)
            releases = device["releases"]
            if version:
                release = next((r for r in releases if version in (
                    r["version"], *r.get("aliases", [])
                )), None)
                if release is None:
                    raise ValueError(f"Unknown version {version!r} for {device['model']}")
            elif len(releases) == 1:
                release = releases[0]
            elif sys.stdin.isatty():
                release = choose(releases, "an updater version", lambda r: r["version"])
            else:
                raise ValueError("Select an updater with --version")
            output = getattr(args, "output", None) or Path(
                f"{device['model']}-{release['version']}"
            )
            if output.exists():
                raise FileExistsError(f"Output directory already exists: {output}")
            print(f"Downloading {device_label(device)} updater {release['version']}…")
            with tempfile.TemporaryDirectory(prefix="razer-updater-") as directory:
                updater = Path(directory) / "updater.exe"
                download(release, updater)
                manifest = extract(updater, output)
        print(f"Extracted {len(manifest['payloads'])} payload(s) to {output}")
        return 0
    except (OSError, ValueError, zipfile.BadZipFile, NotImplementedError, RuntimeError) as exc:
        print(f"razer-updater-extract: {exc}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nSelection cancelled.", file=sys.stderr)
        return 130
