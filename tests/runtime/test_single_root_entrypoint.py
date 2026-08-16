from pathlib import Path
import unittest


class RootLayoutTest(unittest.TestCase):

    def test_gate2_runtime_layout(self):
        root = Path(__file__).resolve().parents[2]

        # Publiczny ręczny launcher budowanego modułu światów.
        self.assertTrue(
            (root / "SSI_V5_RUN.py").is_file()
        )

        # Kod Bramy 2 ma żyć w pakiecie, a nie jako kolejne
        # luźne moduły w głównym katalogu istniejącego SSI.
        required = [
            root / "ssi_v5" / "runtime" / "controller.py",
            root / "ssi_v5" / "runtime" / "starter_contract.py",
            root / "ssi_v5" / "compute" / "fabric.py",
            root / "ssi_v5" / "compute" / "node_worker.py",
            root / "ssi_v5" / "worlds" / "registry.py",
            root / "ssi_v5" / "worlds" / "legacy_factory.py",
        ]

        for path in required:
            self.assertTrue(
                path.is_file(),
                f"Missing Gate 2 module: {path}"
            )

        # Nie ingerujemy w istniejące pliki głównego SSI,
        # takie jak start_ssi.py i jego aktywne moduły.


if __name__ == "__main__":
    unittest.main()