"""Discover official updater links and merge releases that pass extraction."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from http.client import HTTPException
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from urllib.parse import urljoin, urlsplit
from urllib.request import urlopen
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from razer_updater_extract.extract import MAX_UPDATER, extract

SUPPORT = "https://mysupport.razer.com"
INDEX = SUPPORT + "/app/answers/detail/a_id/4166/"
DETAIL = re.compile(r"/app/answers/detail/a_id/(\d+)")
TITLE = re.compile(r"^(?:Razer )?(.+?) Firmware Updat(?:er|e)\s*\|\s*(.+)", re.I)
MODEL = re.compile(r"R[A-Z]\d{2}-\d+[xX]?")
VERSION = re.compile(r"Firmware(?: updat(?:er|e))?\s+v(\d+(?:\.\d+)+(?:[_ ]r\d+)?)", re.I)
CATEGORIES = {
    "RZ01": "mouse",
    "RZ02": "mouse mat",
    "RZ03": "keyboard",
    "RZ04": "headset",
    "RZ05": "speaker",
    "RZ06": "controller",
}


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.events = []
        self.feed(html)

    def handle_data(self, data):
        self.events.append(("text", data))

    def handle_starttag(self, tag, attrs):
        if tag == "a" and (href := dict(attrs).get("href")):
            self.events.append(("link", href))


def read_page(url):
    with urlopen(url, timeout=30) as response:
        return response.read(8 * 1024 * 1024).decode("utf-8", errors="replace")


def is_download(url):
    parts = urlsplit(url)
    host = parts.hostname or ""
    return parts.scheme == "https" and (host == "rzr.to" or (
        host.endswith(".razer.com") and parts.path.lower().endswith(".exe")
    ))


def discover(source, html):
    page = Page(html)
    title = next((m for kind, value in page.events if kind == "text"
                  if (m := TITLE.match(value.strip()))), None)
    if not title:
        return
    name, model_text = title.groups()
    models = MODEL.findall(model_text)
    version = None
    for kind, value in page.events:
        if kind == "text":
            if match := VERSION.search(value):
                version = match[1].replace(" ", "_")
            elif value.strip().lower().startswith(("release notes", "step-by-step", "update process")):
                version = None
        if kind != "link" or not version:
            continue
        url = urljoin(source, value)
        if is_download(url):
            for model in models:
                yield dict(name=name.strip(), model=model, version=version,
                           url=url, source=source)


def scan(start, saved, articles):
    html = saved.read_text(encoding="utf-8") if saved else ""
    if not saved and not articles:
        html = read_page(start)
    links = set()
    for kind, href in Page(html).events:
        if kind != "link":
            continue
        url = urlsplit(urljoin(start, href))
        if url.hostname == "mysupport.razer.com":
            if match := DETAIL.search(url.path):
                links.add(f"{SUPPORT}{match[0]}/")
    yield from discover(start, html)
    for path in sorted(articles.glob("*.html")) if articles else []:
        if path.stem.isdecimal():
            url = f"{SUPPORT}/app/answers/detail/a_id/{path.stem}/"
            links.discard(url)
            yield from discover(url, path.read_text(encoding="utf-8"))
    if not links and not articles and "pre_page_check" in html:
        raise ValueError("Razer returned its browser check. Save the index in a browser and pass --html.")
    for url in sorted(links - {start}):
        try:
            article = read_page(url)
            if "<title>Oracle B2C Service</title>" in article:
                raise ValueError("Browser check; save this article as its answer ID plus .html")
            yield from discover(url, article)
        except (OSError, HTTPException, ValueError) as error:
            print(f"{url}: {error}", file=sys.stderr)


def verify(candidate):
    try:
        if not is_download(candidate["url"]):
            raise ValueError("Expected an official Razer download link")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "updater.exe"
            with urlopen(candidate["url"], timeout=45) as response, path.open("wb") as output:
                if urlsplit(response.geturl()).scheme != "https":
                    raise ValueError("Updater download redirected away from HTTPS")
                remaining = MAX_UPDATER
                while chunk := response.read(1024 * 1024):
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise ValueError("Download exceeds the supported size limit")
                    output.write(chunk)
            manifest = extract(path, Path(directory) / "output")
            return candidate | {"sha256": manifest["updater_sha256"]}
    except (OSError, HTTPException, ValueError, RuntimeError, zipfile.BadZipFile) as error:
        print(f'{candidate["model"]} {candidate["version"]}: {error}', file=sys.stderr)
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", default=INDEX, help="Official index or search-results URL")
    parser.add_argument("--html", type=Path, help="Saved HTML for the index or results page")
    parser.add_argument("--articles", type=Path, help="Directory of saved detail pages named ANSWER_ID.html")
    parser.add_argument("--candidates", type=Path, help="JSON array of known release candidates")
    parser.add_argument("--registry", type=Path, default=Path(__file__).resolve().parents[1] /
                        "razer_updater_extract/registry.json")
    parser.add_argument("--write", action="store_true", help="Write verified additions to the registry")
    args = parser.parse_args()
    try:
        candidates = (json.loads(args.candidates.read_text()) if args.candidates
                      else list(scan(args.index, args.html, args.articles)))
    except (OSError, HTTPException, ValueError) as error:
        parser.exit(1, f"{error}\n")
    registry = json.loads(args.registry.read_text())
    devices = {device["model"].casefold(): device for device in registry["devices"]}
    known = {(device["model"].casefold(), release["url"]) for device in devices.values()
             for release in device["releases"]}
    pending = {(item["model"].casefold(), item["url"]): item for item in candidates
               if (item["model"].casefold(), item["url"]) not in known}
    print(f"Checking {len(pending)} new candidates", file=sys.stderr)
    added = 0
    with ThreadPoolExecutor(max_workers=4) as executor:
        for item in executor.map(verify, pending.values()):
            if item is None:
                continue
            name, model = item.pop("name"), item.pop("model")
            device = devices.setdefault(model.casefold(), {
                "name": name,
                "model": model,
                "category": CATEGORIES.get(model[:4], "other"),
                "releases": [],
            })
            if any(item["sha256"] == release["sha256"] for release in device["releases"]):
                continue
            if any(item["version"] in [release["version"], *release.get("aliases", [])]
                   for release in device["releases"]):
                print(f'{model} {item["version"]}: multiple distinct downloads, review {item["url"]}',
                      file=sys.stderr)
                continue
            device["releases"].append(item)
            added += 1
            print(f'{model} {name} {item["version"]}', file=sys.stderr)
    registry["devices"] = list(devices.values())
    result = json.dumps(registry, indent=2) + "\n"
    if args.write:
        temporary = args.registry.with_suffix(".tmp")
        temporary.write_text(result)
        temporary.replace(args.registry)
    else:
        print(result, end="")
    print(f"{added} verified additions", file=sys.stderr)


if __name__ == "__main__":
    main()
