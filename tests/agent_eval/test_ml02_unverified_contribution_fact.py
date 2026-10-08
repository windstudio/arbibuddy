from pathlib import Path
import unittest

from scripts.model_led_agent_eval import load_journey


class Ml02UnverifiedContributionFactTests(unittest.TestCase):
    def test_ml02_can_answer_unknown_social_contribution_status(self):
        journey = load_journey(
            Path(__file__).resolve().parents[2]
            / "evals"
            / "model-led"
            / "codex-claude-termination-high-risk.yaml"
        )
        facts = {fact.id: fact for fact in journey.fact_pool}

        self.assertIn("social_contribution_status_unknown", facts)
        message = facts["social_contribution_status_unknown"].text
        self.assertIn("社保", message)
        self.assertIn("公积金", message)
        self.assertRegex(message, r"不清楚|不确定|尚未核对")
        self.assertIn("不要推断", message)


if __name__ == "__main__":
    unittest.main()
