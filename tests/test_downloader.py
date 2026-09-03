import unittest
from pathlib import Path

from skill_auto.downloader import parse_github_tree_link


class DownloaderTest(unittest.TestCase):
    def test_parse_github_repo_link(self) -> None:
        repo_url, branch, subdir = parse_github_tree_link("https://github.com/blader/humanizer")

        self.assertEqual(repo_url, "https://github.com/blader/humanizer.git")
        self.assertIsNone(branch)
        self.assertIsNone(subdir)

    def test_parse_github_tree_subdirectory_link(self) -> None:
        repo_url, branch, subdir = parse_github_tree_link(
            "https://github.com/nlink-jp/meeting-notes/tree/main/meeting-notes"
        )

        self.assertEqual(repo_url, "https://github.com/nlink-jp/meeting-notes.git")
        self.assertEqual(branch, "main")
        self.assertEqual(subdir, Path("meeting-notes"))


if __name__ == "__main__":
    unittest.main()
