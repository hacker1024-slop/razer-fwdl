import unittest

from scripts.update_registry import discover


class DiscoveryTests(unittest.TestCase):
    def test_discovery_pairs_releases_with_models_and_official_downloads(self):
        source = "https://mysupport.razer.com/app/answers/detail/a_id/1650/"
        html = """
        <h1>Razer BlackWidow 2019 Firmware Updater | RZ03-02860, RZ03-02861</h1>
        <a href="https://rzr.to/intro">Unrelated link</a>
        <h3>Firmware v1.01 r2</h3>
        <a href="https://example.com/updater.exe">External link</a>
        <a href="https://rzr.to/oyVPZ">Download</a>
        <h3>Firmware updater v1.00</h3>
        <a href="https://dl.razerzone.com/updater.exe">Unrecognized host</a>
        <a href="https://dl.razer.com/updater.exe">Download</a>
        """
        candidates = list(discover(source, html))
        self.assertEqual(
            [(entry["model"], entry["version"], entry["url"]) for entry in candidates],
            [
                ("RZ03-02860", "1.01_r2", "https://rzr.to/oyVPZ"),
                ("RZ03-02861", "1.01_r2", "https://rzr.to/oyVPZ"),
                ("RZ03-02860", "1.00", "https://dl.razer.com/updater.exe"),
                ("RZ03-02861", "1.00", "https://dl.razer.com/updater.exe"),
            ],
        )
