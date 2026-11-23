package com.tvcaster.player;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.SurfaceView;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;

import androidx.media3.common.C;
import androidx.media3.common.Format;
import androidx.media3.common.MediaItem;
import androidx.media3.common.PlaybackException;
import androidx.media3.common.PlaybackParameters;
import androidx.media3.common.Player;
import androidx.media3.common.TrackSelectionOverride;
import androidx.media3.common.Tracks;
import androidx.media3.datasource.DefaultDataSource;
import androidx.media3.datasource.DefaultHttpDataSource;
import androidx.media3.exoplayer.DefaultLoadControl;
import androidx.media3.exoplayer.ExoPlayer;
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.HashMap;
import java.util.Iterator;
import java.util.List;
import java.util.Map;

public class MainActivity extends Activity implements Player.Listener, Runnable {
    private static final String DEFAULT_SERVER_URL = "http://192.168.1.116:8080/tv";
    private static final String PREFS_NAME = "tvcaster_prefs";
    private static final String KEY_SERVER_URL = "server_url";

    SurfaceView surfaceView;
    WebView webView;
    ExoPlayer exoPlayer;
    Handler mainHandler;
    String loadedPageUrl = null;
    String activeStreamUrl = null;

    volatile int cachedPositionSec = 0;
    volatile int cachedDurationSec = 0;
    volatile boolean cachedIsPlaying = false;
    volatile String cachedAudioTracksJson = "[]";
    int pendingSeekSec = 0;
    float pendingSpeed = 1.0f;

    @Override
    public void run() {
        try {
            if (exoPlayer != null) {
                cachedIsPlaying = exoPlayer.isPlaying();
                long posMs = exoPlayer.getCurrentPosition();
                long durMs = exoPlayer.getDuration();
                if (posMs >= 0) {
                    cachedPositionSec = (int) (posMs / 1000L);
                }
                if (durMs > 0 && durMs != C.TIME_UNSET) {
                    cachedDurationSec = (int) (durMs / 1000L);
                }
            }
        } catch (Exception ignored) {
        }
        if (mainHandler != null) {
            mainHandler.postDelayed(this, 300);
        }
    }

    @Override
    public void onPlaybackStateChanged(int playbackState) {
        try {
            if (playbackState == Player.STATE_READY && exoPlayer != null) {
                long durMs = exoPlayer.getDuration();
                if (durMs > 0 && durMs != C.TIME_UNSET) {
                    cachedDurationSec = (int) (durMs / 1000L);
                }
                if (pendingSeekSec > 0) {
                    exoPlayer.seekTo(pendingSeekSec * 1000L);
                    cachedPositionSec = pendingSeekSec;
                    pendingSeekSec = 0;
                }
                if (Math.abs(pendingSpeed - 1.0f) > 0.02f) {
                    applyPlaybackSpeed(pendingSpeed);
                }
                cachedIsPlaying = exoPlayer.isPlaying();
            }
        } catch (Exception ignored) {
        }
    }

    @Override
    public void onTracksChanged(Tracks tracks) {
        try {
            JSONArray arr = new JSONArray();
            List<Tracks.Group> groups = tracks.getGroups();
            int audioIdx = 0;
            for (int g = 0; g < groups.size(); g++) {
                Tracks.Group group = groups.get(g);
                if (group.getType() != C.TRACK_TYPE_AUDIO) {
                    continue;
                }
                for (int t = 0; t < group.length; t++) {
                    Format fmt = group.getTrackFormat(t);
                    JSONObject obj = new JSONObject();
                    obj.put("index", audioIdx);
                    obj.put("lang", fmt.language != null ? fmt.language : "");
                    obj.put("label", fmt.label != null ? fmt.label : ("Track " + (audioIdx + 1)));
                    obj.put("codec", fmt.sampleMimeType != null ? fmt.sampleMimeType : "");
                    obj.put("selected", group.isTrackSelected(t));
                    arr.put(obj);
                    audioIdx++;
                }
            }
            cachedAudioTracksJson = arr.toString();
        } catch (Exception ignored) {
        }
    }

    @Override
    public void onPlayerError(PlaybackException error) {
        try {
            if (webView != null) {
                webView.evaluateJavascript("if(window.onNativePlayerError)window.onNativePlayerError();", null);
            }
        } catch (Exception ignored) {
        }
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

        surfaceView = new SurfaceView(this);
        FrameLayout.LayoutParams videoParams = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
                Gravity.CENTER
        );
        surfaceView.setLayoutParams(videoParams);

        // Bigger buffers than the defaults (50 s max, 5 s to resume after a stall): a slow source then causes
        // rare longer pauses instead of frequent one-second stalls.
        DefaultLoadControl loadControl = new DefaultLoadControl.Builder()
                .setBufferDurationsMs(30000, 120000, 2500, 12000)
                .build();
        exoPlayer = new ExoPlayer.Builder(this).setLoadControl(loadControl).build();
        exoPlayer.setVideoSurfaceView(surfaceView);
        exoPlayer.addListener(this);

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

        root.addView(surfaceView);
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

    void startStreamInternal(String url, String headersJson, int startAtSec, float speed) {
        if (url == null || exoPlayer == null) {
            return;
        }
        if (url.equals(activeStreamUrl) && exoPlayer.isPlaying()) {
            return;
        }
        activeStreamUrl = url;
        pendingSeekSec = Math.max(0, startAtSec);
        pendingSpeed = speed > 0 ? speed : 1.0f;
        cachedPositionSec = pendingSeekSec;
        cachedDurationSec = 0;
        cachedAudioTracksJson = "[]";

        Map<String, String> headersMap = new HashMap<String, String>();
        String userAgent = "Mozilla/5.0 (Linux; Android 11; Android TV) AppleWebKit/537.36";
        if (headersJson != null && headersJson.length() > 2) {
            try {
                JSONObject obj = new JSONObject(headersJson);
                Iterator<String> keys = obj.keys();
                while (keys.hasNext()) {
                    String k = keys.next();
                    String v = obj.optString(k, "");
                    if (v != null && v.length() > 0) {
                        if ("user-agent".equalsIgnoreCase(k)) {
                            userAgent = v;
                        } else {
                            headersMap.put(k, v);
                        }
                    }
                }
            } catch (Exception ignored) {
            }
        }

        DefaultHttpDataSource.Factory httpFactory = new DefaultHttpDataSource.Factory()
                .setUserAgent(userAgent)
                .setAllowCrossProtocolRedirects(true)
                .setConnectTimeoutMs(15000)
                .setReadTimeoutMs(20000)
                .setDefaultRequestProperties(headersMap);

        DefaultMediaSourceFactory mediaSourceFactory = new DefaultMediaSourceFactory(this)
                .setDataSourceFactory(new DefaultDataSource.Factory(this, httpFactory));

        MediaItem mediaItem = MediaItem.fromUri(Uri.parse(url));
        exoPlayer.setMediaSource(mediaSourceFactory.createMediaSource(mediaItem));
        if (pendingSeekSec > 0) {
            exoPlayer.seekTo(pendingSeekSec * 1000L);
        }
        exoPlayer.setPlaybackParameters(new PlaybackParameters(pendingSpeed));
        exoPlayer.prepare();
        exoPlayer.setPlayWhenReady(true);
        cachedIsPlaying = true;
    }

    void applyPlaybackSpeed(float speed) {
        pendingSpeed = speed > 0 ? speed : 1.0f;
        if (exoPlayer != null) {
            try {
                exoPlayer.setPlaybackParameters(new PlaybackParameters(pendingSpeed));
            } catch (Exception ignored) {
            }
        }
    }

    void selectAudioTrackInternal(int targetIndex) {
        if (exoPlayer == null) {
            return;
        }
        try {
            Tracks tracks = exoPlayer.getCurrentTracks();
            List<Tracks.Group> groups = tracks.getGroups();
            int currentIdx = 0;
            for (int g = 0; g < groups.size(); g++) {
                Tracks.Group group = groups.get(g);
                if (group.getType() != C.TRACK_TYPE_AUDIO) {
                    continue;
                }
                for (int t = 0; t < group.length; t++) {
                    if (currentIdx == targetIndex) {
                        exoPlayer.setTrackSelectionParameters(
                                exoPlayer.getTrackSelectionParameters()
                                        .buildUpon()
                                        .setOverrideForType(new TrackSelectionOverride(group.getMediaTrackGroup(), t))
                                        .build()
                        );
                        return;
                    }
                    currentIdx++;
                }
            }
        } catch (Exception ignored) {
        }
    }

    private static String mapKeyCode(int keyCode) {
        switch (keyCode) {
            case KeyEvent.KEYCODE_DPAD_UP:
                return "UP";
            case KeyEvent.KEYCODE_DPAD_DOWN:
                return "DOWN";
            case KeyEvent.KEYCODE_DPAD_LEFT:
                return "LEFT";
            case KeyEvent.KEYCODE_DPAD_RIGHT:
                return "RIGHT";
            case KeyEvent.KEYCODE_DPAD_CENTER:
            case KeyEvent.KEYCODE_ENTER:
            case KeyEvent.KEYCODE_NUMPAD_ENTER:
                return "ENTER";
            case KeyEvent.KEYCODE_BACK:
            case KeyEvent.KEYCODE_ESCAPE:
                return "BACK";
            case KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE:
            case KeyEvent.KEYCODE_MEDIA_PLAY:
            case KeyEvent.KEYCODE_MEDIA_PAUSE:
                return "PLAY_PAUSE";
            case KeyEvent.KEYCODE_MEDIA_REWIND:
                return "REWIND";
            case KeyEvent.KEYCODE_MEDIA_FAST_FORWARD:
                return "FF";
            default:
                return null;
        }
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        String mapped = mapKeyCode(event.getKeyCode());
        if (mapped != null) {
            if (event.getAction() == KeyEvent.ACTION_DOWN && webView != null) {
                boolean isNonRepeatable = "ENTER".equals(mapped) || "BACK".equals(mapped) || "PLAY_PAUSE".equals(mapped);
                if (!isNonRepeatable || event.getRepeatCount() == 0) {
                    webView.evaluateJavascript("if(window.onTvKey)window.onTvKey('" + mapped + "');", null);
                }
            }
            return true;
        }
        return super.dispatchKeyEvent(event);
    }

    @Override
    protected void onDestroy() {
        if (mainHandler != null) {
            mainHandler.removeCallbacks(this);
        }
        if (exoPlayer != null) {
            try {
                exoPlayer.release();
            } catch (Exception ignored) {
            }
            exoPlayer = null;
        }
        super.onDestroy();
    }

    static class BridgeTask implements Runnable {
        private final MainActivity act;
        private final int cmd;
        private final String url;
        private final String headersJson;
        private final int intVal;
        private final float floatVal;

        BridgeTask(MainActivity act, int cmd, String url, String headersJson, int intVal, float floatVal) {
            this.act = act;
            this.cmd = cmd;
            this.url = url;
            this.headersJson = headersJson;
            this.intVal = intVal;
            this.floatVal = floatVal;
        }

        @Override
        public void run() {
            try {
                if (cmd == 1) { // loadStream / loadStreamWithHeaders
                    act.startStreamInternal(url, headersJson, intVal, floatVal);
                } else if (cmd == 2) { // play
                    if (act.exoPlayer != null) {
                        act.exoPlayer.play();
                    }
                    act.cachedIsPlaying = true;
                } else if (cmd == 3) { // pause
                    if (act.exoPlayer != null) {
                        act.exoPlayer.pause();
                    }
                    act.cachedIsPlaying = false;
                } else if (cmd == 4) { // stop
                    act.activeStreamUrl = null;
                    if (act.exoPlayer != null) {
                        act.exoPlayer.stop();
                    }
                    act.cachedIsPlaying = false;
                    act.cachedPositionSec = 0;
                } else if (cmd == 5) { // seekTo
                    int clamped = Math.max(0, intVal);
                    act.cachedPositionSec = clamped;
                    if (act.exoPlayer != null) {
                        act.exoPlayer.seekTo(clamped * 1000L);
                    }
                } else if (cmd == 6) { // setSpeed
                    act.applyPlaybackSpeed(floatVal);
                } else if (cmd == 7) { // selectAudioTrack
                    act.selectAudioTrackInternal(intVal);
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
            act.mainHandler.post(new BridgeTask(act, 1, url, null, startAtSec, speed));
        }

        @JavascriptInterface
        public void loadStreamWithHeaders(String url, String headersJson, int startAtSec, float speed) {
            act.mainHandler.post(new BridgeTask(act, 1, url, headersJson, startAtSec, speed));
        }

        @JavascriptInterface
        public void play() {
            act.mainHandler.post(new BridgeTask(act, 2, null, null, 0, 1.0f));
        }

        @JavascriptInterface
        public void pause() {
            act.mainHandler.post(new BridgeTask(act, 3, null, null, 0, 1.0f));
        }

        @JavascriptInterface
        public void stop() {
            act.mainHandler.post(new BridgeTask(act, 4, null, null, 0, 1.0f));
        }

        @JavascriptInterface
        public void seekTo(int sec) {
            act.mainHandler.post(new BridgeTask(act, 5, null, null, sec, 1.0f));
        }

        @JavascriptInterface
        public void setSpeed(float speed) {
            act.mainHandler.post(new BridgeTask(act, 6, null, null, 0, speed));
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

        @JavascriptInterface
        public String getAudioTracks() {
            return act.cachedAudioTracksJson;
        }

        @JavascriptInterface
        public void selectAudioTrack(int index) {
            act.mainHandler.post(new BridgeTask(act, 7, null, null, index, 1.0f));
        }
    }
}
