import unittest

from skill_auto.cli import cases_with_env_context, normalize_env_values, secret_values_for_env


class CliEnvContextTest(unittest.TestCase):
    def test_cases_with_env_context_prefixes_required_keys(self) -> None:
        cases = [{"id": "core-flow", "prompt": "请使用 wechat-cover Skill 生成封面方案。"}]

        updated = cases_with_env_context(cases, {"REDFOX_API_KEY": "redacted_secret_value"})

        self.assertIn("REDFOX_API_KEY=redacted_secret_value", updated[0]["prompt"])
        self.assertTrue(updated[0]["prompt"].endswith("请使用 wechat-cover Skill 生成封面方案。"))
        self.assertEqual(updated[0]["env_context_keys"], ["REDFOX_API_KEY"])

    def test_secret_values_for_env_returns_manifest_env_values(self) -> None:
        values = secret_values_for_env({"REDFOX_API_KEY": "redacted_secret_value", "OTHER_TOKEN": "other_secret_value"})

        self.assertEqual(values, ["redacted_secret_value", "other_secret_value"])

    def test_normalize_env_values_accepts_mapping(self) -> None:
        self.assertEqual(
            normalize_env_values({"REDFOX_API_KEY": "redacted_secret_value", "EMPTY": ""}),
            {"REDFOX_API_KEY": "redacted_secret_value"},
        )

    def test_normalize_env_values_accepts_key_value_list(self) -> None:
        self.assertEqual(
            normalize_env_values(["REDFOX_API_KEY=redacted_secret_value", "OTHER_TOKEN=other_secret_value"]),
            {"REDFOX_API_KEY": "redacted_secret_value", "OTHER_TOKEN": "other_secret_value"},
        )

    def test_normalize_env_values_accepts_export_string(self) -> None:
        self.assertEqual(
            normalize_env_values("export REDFOX_API_KEY='redacted_secret_value'\nOTHER_TOKEN=\"other_secret_value\""),
            {"REDFOX_API_KEY": "redacted_secret_value", "OTHER_TOKEN": "other_secret_value"},
        )


if __name__ == "__main__":
    unittest.main()
