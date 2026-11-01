from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WorkflowSafetyTests(unittest.TestCase):
    def test_automation_does_not_request_host_privileges_or_nested_systemd(self):
        files = list((ROOT / ".github/workflows").glob("*.yml"))
        files += list((ROOT / "automation").glob("*.sh"))
        for path in files:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertNotRegex(path.read_text(),
                                    r"--privileged|--cap-add|--device(?:=|\s)|--pid[= ]host|"
                                    r"systemd-machine-id-setup|/systemd/systemd|extra-x86_64-build|arch-nspawn")


if __name__ == "__main__":
    unittest.main()
