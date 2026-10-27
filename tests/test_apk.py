import os
import sys
import threading
import unittest
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestAndroidTVApk(unittest.TestCase):
    def test_android_manifest_exists(self):
        manifest_path = os.path.join(ROOT_DIR, "android-tv", "app", "src", "main", "AndroidManifest.xml")
        self.assertTrue(os.path.isfile(manifest_path), "AndroidManifest.xml missing")
        with open(manifest_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("com.tvcaster.player", content)
        self.assertIn("android.intent.category.LEANBACK_LAUNCHER", content)
        self.assertIn("android.software.leanback", content)
        self.assertIn("android.hardware.touchscreen", content)

    def test_main_activity_exists(self):
        java_path = os.path.join(
            ROOT_DIR, "android-tv", "app", "src", "main", "java", "com", "tvcaster", "player", "MainActivity.java"
        )
        self.assertTrue(os.path.isfile(java_path), "MainActivity.java missing")
        with open(java_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("AndroidBridge", content)
        self.assertIn("WebView", content)
        self.assertIn("FLAG_KEEP_SCREEN_ON", content)
        self.assertIn("ExoPlayer", content)
        self.assertIn("DefaultHttpDataSource", content)
        self.assertIn("SurfaceView", content)
        self.assertIn("loadStreamWithHeaders", content)
        self.assertIn("getAudioTracks", content)
        self.assertIn("selectAudioTrack", content)

    def test_compiled_apk_valid_and_served(self):
        apk_path = os.path.join(ROOT_DIR, "static", "tv-caster-player.apk")
        self.assertTrue(os.path.isfile(apk_path), "Compiled APK static/tv-caster-player.apk missing")
        self.assertTrue(zipfile.is_zipfile(apk_path), "APK is not a valid zip archive")
        with zipfile.ZipFile(apk_path, "r") as zf:
            names = zf.namelist()
            self.assertIn("AndroidManifest.xml", names)
            self.assertIn("classes.dex", names)
            self.assertTrue(any(n.startswith("META-INF/") for n in names), "APK is not signed")

        httpd = server.ThreadedServer(("127.0.0.1", 0), server.TVCasterHandler)
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/player.apk", timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                self.assertIn("application/vnd.android.package-archive", resp.headers.get("Content-Type", ""))
                data = resp.read()
                self.assertEqual(len(data), os.path.getsize(apk_path))
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
