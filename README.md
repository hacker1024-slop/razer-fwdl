# Razer Updater Extract

Extract firmware from supported Razer Windows updater executables on Linux,
macOS, or Windows. The tool reads the executable as data. It never starts the
updater or communicates with a device.

Requires Python 3.11 or newer. There are no runtime dependencies.

```sh
python -m pip install .
razer-updater-extract
```

Running without arguments shows the devices in the registry. The tool prompts
for a version when a device has more than one registered release. You can also
run `python -m razer_updater_extract` directly from the source tree.

```sh
razer-updater-extract list
razer-updater-extract download --device RZ03-0500 --version 1.02.01_r1 -o blackwidow
razer-updater-extract extract /path/to/updater.exe -o extracted
```

Use a new output directory. Downloads are verified against the executable's
SHA-256 in the registry. Local files can be extracted offline and need no
registry entry.

## Verified releases

| Device | Updater release | Payloads |
| --- | --- | ---: |
| [BlackWidow V4 75%](https://mysupport.razer.com/app/answers/detail/a_id/20595/) | 1.02.01_r1 | 1 |
| [BlackWidow V3 Pro](https://mysupport.razer.com/app/answers/detail/a_id/4728/) | 3.03.00_r1 | 2 |
| [DeathStalker V2](https://mysupport.razer.com/app/answers/detail/a_id/6119/) | 1.01.00_r1 | 1 |
| [Cobra Pro](https://mysupport.razer.com/app/answers/detail/a_id/13282/) | 3.00.02_r1 | 2 |
| [DeathAdder V2 Pro](https://mysupport.razer.com/app/answers/detail/a_id/4557/) | 2.05.01_r1 | 2 |

The bundled [JSON registry](razer_updater_extract/registry.json) records each
model, release, official support page, download link and executable hash. Add
other releases after verifying their official source and testing extraction.

## Output and compatibility

Each firmware stream gets an Intel HEX file and one binary per contiguous
address range. `manifest.json` identifies the resource stream, firmware
version, optional entry point, and each binary's load address, size and SHA-256.
Addresses in the JSON are integers. A wireless updater can include multiple
components. The tool keeps their resource identifiers and does not guess their
roles.

The supported format is an executable with a ZIP containing
`DeviceUpdater.resources`. The tool validates the resource indices and Intel
HEX checksums and rejects overlaps. Other installer or payload formats require
a different extractor.

The output represents the update payload. It may omit bootloaders, settings,
device-specific data or flash outside the update ranges. It is not a readback
from a physical device.

This tool was AI-generated. Review extracted images before using them with
any flashing tool. There is no guarantee against device damage if you flash
the output.

```sh
python -m unittest discover -s tests
```
