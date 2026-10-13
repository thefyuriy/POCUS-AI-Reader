# SPDX-License-Identifier: AGPL-3.0-only
"""The medical wording rule: never describe a distinction in medicine as formal/informal or official/unofficial."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BANNED = re.compile(r"\b(informal|formal|unofficial|official)\b", re.IGNORECASE)


class WordingTest(unittest.TestCase):
    def test_no_formal_or_official_in_user_facing_text(self):
        files = list((ROOT / "pocus_ai_reader" / "static").glob("*")) + [ROOT / "README.md", ROOT / "NOTICE.md"]
        files += list((ROOT / "docs").glob("*.md"))
        hits = [f"{f.name}: {m.group(0)}" for f in files for m in BANNED.finditer(f.read_text(encoding="utf-8"))]
        self.assertEqual(hits, [], "Use 'consultative echocardiogram'; never formal/informal or official/unofficial.")


if __name__ == "__main__":
    unittest.main()
