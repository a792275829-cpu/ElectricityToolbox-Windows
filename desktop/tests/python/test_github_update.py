from pathlib import Path
import sys
import unittest

TOOLBOX_TEST_TAGS = ()
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))
from github_update import UpdateError, version_parts


class VersionParserTests(unittest.TestCase):
    def test_numeric_versions_and_invalid_tags(self):
        self.assertGreater(version_parts("v1.10.0"), version_parts("1.9.9"))
        self.assertEqual(version_parts("1.2"), version_parts("1.2.0.0"))
        for tag in ("latest", "1.2-beta", "1..2", "１.２", "1", "1.2.3.4.5"):
            with self.subTest(tag=tag), self.assertRaises(UpdateError):
                version_parts(tag)


if __name__ == "__main__":
    unittest.main()
