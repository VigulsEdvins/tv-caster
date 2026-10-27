import os
import sys
import unittest
from unittest.mock import patch, MagicMock

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, os.path.join(ROOT_DIR, "tools"))


class TestTVDeploy(unittest.TestCase):
    def test_deploy_module_imports_and_finds_adb(self):
        import tv_deploy
        adb_path = tv_deploy.find_adb()
        self.assertTrue(adb_path and os.path.exists(adb_path), "adb binary should be found")

    def test_deploy_to_tv_workflow(self):
        import tv_deploy
        with patch("subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = "connected to 192.168.1.105:5555\nSuccess\n"
            mock_proc.stderr = ""
            mock_run.return_value = mock_proc

            ok, msg = tv_deploy.deploy_to_tv(
                tv_ip="192.168.1.105",
                apk_path=__file__,  # existing file for path check
                server_url="http://192.168.1.116:8080/tv"
            )
            self.assertTrue(ok, msg)
            cmds = [" ".join(call.args[0]) for call in mock_run.call_args_list]
            self.assertTrue(any("connect 192.168.1.105:5555" in c for c in cmds))
            self.assertTrue(any("install -r" in c for c in cmds))
            self.assertTrue(any("com.tvcaster.player/.MainActivity" in c for c in cmds))


if __name__ == "__main__":
    unittest.main()
