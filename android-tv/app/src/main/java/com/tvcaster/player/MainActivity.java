package com.tvcaster.player;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.media.MediaPlayer;
import android.media.PlaybackParams;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.VideoView;

public class MainActivity extends Activity implements MediaPlayer.OnPreparedListener, MediaPlayer.OnErrorListener, Runnable {
    private static final String DEFAULT_SERVER_URL = "http://192.168.1.116:8080/tv";
    private static final String PREFS_NAME = "tvcaster_prefs";
    private static final String KEY_SERVER_URL = "server_url";

    VideoView videoView;
    WebView webView;
    MediaPlayer activeMediaPlayer;
    Handler mainHandler;
    String loadedPageUrl = null;
    String activeStreamUrl = null;

    volatile int cachedPositionSec = 0;
    volatile int cachedDurationSec = 0;
    volatile boolean cachedIsPlaying = false;
    int pendingSeekSec = 0;
    float pendingSpeed = 1.0f;

    @Override
    public void run() {
        try {
            if (videoView != null) {
                cachedIsPlaying = videoView.isPlaying();
                int posMs = videoView.getCurrentPosition();
                int durMs = videoView.getDuration();
                if (posMs > 0) {
                    cachedPositionSec = posMs / 1000;
                }
                if (durMs > 0) {
                    cachedDurationSec = durMs / 1000;
                }
            }
        } catch (Exception ignored) {
        }
        if (mainHandler != null) {
            mainHandler.postDelayed(this, 300);
        }
    }

    @Override
    public void onPrepared(MediaPlayer mp) {
        activeMediaPlayer = mp;
        try {
            int durMs = mp.getDuration();
            if (durMs > 0) {
                cachedDurationSec = durMs / 1000;
            }
            if (pendingSeekSec > 0) {
                mp.seekTo(pendingSeekSec * 1000);
                pendingSeekSec = 0;
            }
            mp.start();
            cachedIsPlaying = true;
            if (Math.abs(pendingSpeed - 1.0f) > 0.02f) {
                applyPlaybackSpeed(pendingSpeed);
            }
        } catch (Exception ignored) {
        }
    }

    @Override
    public boolean onError(MediaPlayer mp, int what, int extra) {
        return true;
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        requestWindowFeature(Window.FEATURE_NO_TITLE);
        getWindow().setFlags(
                WindowManager.LayoutParams.FLAG_FULLSCREEN | WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON,
                WindowManager.LayoutParams.FLAG_FULLSCREEN | WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
        );

        mainHandler = new Handler(Looper.getMainLooper());

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);

        videoView = new VideoView(this);
        FrameLayout.LayoutParams videoParams = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
                Gravity.CENTER
        );
        videoView.setLayoutParams(videoParams);
        videoView.setOnPreparedListener(this);
        videoView.setOnErrorListener(this);

        webView = new WebView(this);
        FrameLayout.LayoutParams webParams = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
        );
        webView.setLayoutParams(webParams);
        webView.setBackgroundColor(Color.TRANSPARENT);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.JELLY_BEAN_MR1) {
            settings.setMediaPlaybackRequiresUserGesture(false);
        }

        webView.setWebViewClient(new WebViewClient());
        webView.setWebChromeClient(new WebChromeClient());
        webView.addJavascriptInterface(new AndroidBridge(this), "AndroidBridge");

        root.addView(videoView);
        root.addView(webView);
        setContentView(root);

        webView.requestFocus();
        loadTargetUrl(getIntent(), true);
        mainHandler.post(this);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        loadTargetUrl(intent, false);
    }

    private void loadTargetUrl(Intent intent, boolean forceLoad) {
        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        String url = prefs.getString(KEY_SERVER_URL, DEFAULT_SERVER_URL);
        if (intent != null && intent.hasExtra("server_url")) {
            String extraUrl = intent.getStringExtra("server_url");
            if (extraUrl != null && extraUrl.startsWith("http")) {
                url = extraUrl;
                prefs.edit().putString(KEY_SERVER_URL, url).apply();
            }
        }
        if (forceLoad || loadedPageUrl == null || !loadedPageUrl.equals(url)) {
            loadedPageUrl = url;
            webView.loadUrl(url);
        }
    }

    void applyPlaybackSpeed(float speed) {
        pendingSpeed = speed;
        if (activeMediaPlayer != null && Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            try {
                PlaybackParams params = new PlaybackParams();
                params.setSpeed(speed);
                activeMediaPlayer.setPlaybackParams(params);
            } catch (Exception ignored) {
            }
        }
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        if (event.getKeyCode() == KeyEvent.KEYCODE_BACK) {
            if (event.getAction() == KeyEvent.ACTION_DOWN && webView != null) {
                webView.evaluateJavascript(
                        "window.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', keyCode: 27}));",
                        null
                );
            }
            return true;
        }
        if (webView != null) {
            return webView.dispatchKeyEvent(event);
        }
        return super.dispatchKeyEvent(event);
    }

    @Override
    protected void onDestroy() {
        if (mainHandler != null) {
            mainHandler.removeCallbacks(this);
        }
        super.onDestroy();
    }

    static class BridgeTask implements Runnable {
        private final MainActivity act;
        private final int cmd;
        private final String url;
        private final int intVal;
        private final float floatVal;

        BridgeTask(MainActivity act, int cmd, String url, int intVal, float floatVal) {
            this.act = act;
            this.cmd = cmd;
            this.url = url;
            this.intVal = intVal;
            this.floatVal = floatVal;
        }

        @Override
        public void run() {
            try {
                if (cmd == 1) { // loadStream
                    if (url != null && url.equals(act.activeStreamUrl) && act.videoView.isPlaying()) {
                        return;
                    }
                    act.activeStreamUrl = url;
                    act.pendingSeekSec = Math.max(0, intVal);
                    act.pendingSpeed = floatVal > 0 ? floatVal : 1.0f;
                    act.cachedPositionSec = act.pendingSeekSec;
                    act.cachedDurationSec = 0;
                    act.videoView.setVideoURI(Uri.parse(url));
                } else if (cmd == 2) { // play
                    act.videoView.start();
                    act.cachedIsPlaying = true;
                } else if (cmd == 3) { // pause
                    act.videoView.pause();
                    act.cachedIsPlaying = false;
                } else if (cmd == 4) { // stop
                    act.activeStreamUrl = null;
                    act.videoView.stopPlayback();
                    act.cachedIsPlaying = false;
                    act.cachedPositionSec = 0;
                } else if (cmd == 5) { // seekTo
                    int clamped = Math.max(0, intVal);
                    act.cachedPositionSec = clamped;
                    if (act.activeMediaPlayer != null) {
                        act.activeMediaPlayer.seekTo(clamped * 1000);
                    } else {
                        act.videoView.seekTo(clamped * 1000);
                    }
                } else if (cmd == 6) { // setSpeed
                    act.applyPlaybackSpeed(floatVal);
                }
            } catch (Exception ignored) {
            }
        }
    }

    public static class AndroidBridge {
        private final MainActivity act;

        public AndroidBridge(MainActivity act) {
            this.act = act;
        }

        @JavascriptInterface
        public void loadStream(String url, int startAtSec, float speed) {
            act.mainHandler.post(new BridgeTask(act, 1, url, startAtSec, speed));
        }

        @JavascriptInterface
        public void play() {
            act.mainHandler.post(new BridgeTask(act, 2, null, 0, 1.0f));
        }

        @JavascriptInterface
        public void pause() {
            act.mainHandler.post(new BridgeTask(act, 3, null, 0, 1.0f));
        }

        @JavascriptInterface
        public void stop() {
            act.mainHandler.post(new BridgeTask(act, 4, null, 0, 1.0f));
        }

        @JavascriptInterface
        public void seekTo(int sec) {
            act.mainHandler.post(new BridgeTask(act, 5, null, sec, 1.0f));
        }

        @JavascriptInterface
        public void setSpeed(float speed) {
            act.mainHandler.post(new BridgeTask(act, 6, null, 0, speed));
        }

        @JavascriptInterface
        public int getPosition() {
            return act.cachedPositionSec;
        }

        @JavascriptInterface
        public int getDuration() {
            return act.cachedDurationSec;
        }

        @JavascriptInterface
        public boolean isPlaying() {
            return act.cachedIsPlaying;
        }
    }
}
