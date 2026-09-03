import tempfile
import unittest
from pathlib import Path

from skill_auto.builtin_writer import update_builtin_sources
from skill_auto.manifest import load_yaml, write_yaml


class BuiltinWriterTest(unittest.TestCase):
    def test_remote_builtin_writes_source_url_and_removes_stale_bundled_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = root / "skills" / "builtin-sources.yaml"
            sources.parent.mkdir(parents=True)
            write_yaml(
                sources,
                {
                    "schema_version": 1,
                    "bundled_skills": [
                        {
                            "uid": "bsk_weather",
                            "path": "work/weather",
                            "category": "work",
                            "version": "1.0.0",
                            "provider": "LazyMind",
                        }
                    ],
                    "skills": [],
                },
            )

            result = update_builtin_sources(
                root,
                [
                    {
                        "name": "weather",
                        "link": "https://skillhub.cn/skills/clawhub_steipete/weather",
                        "onboard_as": {"builtin": True},
                        "builtin": {
                            "uid": "bsk_weather",
                            "path": "work/weather",
                            "category": "work",
                            "version": "1.0.0",
                            "provider": "LazyMind",
                        },
                    }
                ],
                dry_run=False,
            )

            data = load_yaml(sources)
            self.assertEqual(data["bundled_skills"], [])
            self.assertEqual(
                data["skills"],
                [
                    {
                        "source_url": "https://skillhub.cn/skills/clawhub_steipete/weather",
                        "category": "work",
                        "provider": "LazyMind",
                    }
                ],
            )
            self.assertEqual(result["changes"], ["add remote builtin https://skillhub.cn/skills/clawhub_steipete/weather"])


if __name__ == "__main__":
    unittest.main()
