from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from oraclis_agent import WHO_SOURCE, _fallback


class HealthGuidanceTests(unittest.TestCase):
    def test_guidance_names_source_and_medical_limit(self) -> None:
        answer = _fallback("health_guidance", [{"facts": "Seek immediate care for warning signs."}])
        self.assertIn(WHO_SOURCE, answer)
        self.assertIn("not a diagnosis", answer)


if __name__ == "__main__":
    unittest.main()
