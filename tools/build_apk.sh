#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT_DIR/android-tv/app/src/main"
OUT_APK="$ROOT_DIR/static/tv-caster-player.apk"
CACHE_DIR="/tmp/android-sdk-cache"
MEDIA3_DIR="$CACHE_DIR/media3-jars"

mkdir -p "$ROOT_DIR/static" "$CACHE_DIR" "$MEDIA3_DIR"

if [ ! -f "$CACHE_DIR/android.jar" ]; then
  echo "Downloading Android 28 platform android.jar..."
  curl -sL https://dl.google.com/android/repository/platform-28_r06.zip -o "$CACHE_DIR/platform.zip"
  python3 -c "import zipfile; z=zipfile.ZipFile('$CACHE_DIR/platform.zip'); open('$CACHE_DIR/android.jar','wb').write(z.read('android-9/android.jar'))"
  rm -f "$CACHE_DIR/platform.zip"
fi

if [ ! -f "$CACHE_DIR/d8.jar" ]; then
  echo "Downloading Android build-tools 34 d8.jar..."
  curl -sL https://dl.google.com/android/repository/build-tools_r34-linux.zip -o "$CACHE_DIR/bt.zip"
  python3 -c "import zipfile; z=zipfile.ZipFile('$CACHE_DIR/bt.zip'); open('$CACHE_DIR/d8.jar','wb').write(z.read('android-14/lib/d8.jar'))"
  rm -f "$CACHE_DIR/bt.zip"
fi

fetch_aar_jar() {
  local name="$1"
  local url="$2"
  local out_jar="$MEDIA3_DIR/${name}.jar"
  if [ ! -f "$out_jar" ]; then
    echo "Fetching $name..."
    local tmp_aar="$MEDIA3_DIR/${name}.aar"
    curl -sL "$url" -o "$tmp_aar"
    python3 -c "import zipfile; z=zipfile.ZipFile('$tmp_aar'); open('$out_jar','wb').write(z.read('classes.jar'))"
    rm -f "$tmp_aar"
  fi
}

fetch_jar() {
  local name="$1"
  local url="$2"
  local out_jar="$MEDIA3_DIR/${name}.jar"
  if [ ! -f "$out_jar" ]; then
    echo "Fetching $name..."
    curl -sL "$url" -o "$out_jar"
  fi
}

GMAVEN="https://dl.google.com/android/maven2"
MCENTRAL="https://repo1.maven.org/maven2"
M3_VER="1.2.1"

for mod in media3-common media3-container media3-database media3-datasource media3-decoder media3-extractor media3-exoplayer media3-exoplayer-hls media3-exoplayer-dash; do
  fetch_aar_jar "$mod" "$GMAVEN/androidx/media3/$mod/$M3_VER/$mod-$M3_VER.aar"
done

fetch_aar_jar "annotation-experimental" "$GMAVEN/androidx/annotation/annotation-experimental/1.3.1/annotation-experimental-1.3.1.aar"
fetch_aar_jar "androidx-core" "$GMAVEN/androidx/core/core/1.8.0/core-1.8.0.aar"
fetch_jar "annotation" "$GMAVEN/androidx/annotation/annotation/1.3.0/annotation-1.3.0.jar"
fetch_jar "collection" "$GMAVEN/androidx/collection/collection/1.2.0/collection-1.2.0.jar"
fetch_jar "guava" "$MCENTRAL/com/google/guava/guava/31.1-android/guava-31.1-android.jar"
fetch_jar "failureaccess" "$MCENTRAL/com/google/guava/failureaccess/1.0.1/failureaccess-1.0.1.jar"

echo "Building and signing tv-caster-player.apk in Docker..."
docker run --rm --entrypoint bash \
  -v "$ROOT_DIR:/workspace" \
  -v "$CACHE_DIR:/sdk-cache" \
  nginx:latest -c '
    set -euo pipefail
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends default-jdk-headless aapt zipalign apksigner >/dev/null

    BUILD_DIR=/tmp/apk-build
    mkdir -p "$BUILD_DIR/gen" "$BUILD_DIR/obj" "$BUILD_DIR/dex"

    # 1. Generate R.java and package resources
    aapt package -f -m \
      -J "$BUILD_DIR/gen" \
      -M /workspace/android-tv/app/src/main/AndroidManifest.xml \
      -S /workspace/android-tv/app/src/main/res \
      -I /sdk-cache/android.jar \
      -F "$BUILD_DIR/unaligned.apk"

    # 2. Compile Java sources against android.jar + Media3 ExoPlayer jars
    CLASSPATH="/sdk-cache/android.jar"
    for j in /sdk-cache/media3-jars/*.jar; do
      CLASSPATH="$CLASSPATH:$j"
    done

    javac -source 1.8 -target 1.8 \
      -bootclasspath /sdk-cache/android.jar \
      -classpath "$CLASSPATH" \
      -d "$BUILD_DIR/obj" \
      "$BUILD_DIR/gen/com/tvcaster/player/R.java" \
      /workspace/android-tv/app/src/main/java/com/tvcaster/player/MainActivity.java

    # 3. Convert .class files and Media3 jars to DEX using d8
    java -cp /sdk-cache/d8.jar com.android.tools.r8.D8 \
      --lib /sdk-cache/android.jar \
      --min-api 21 \
      --output "$BUILD_DIR/dex" \
      $(find "$BUILD_DIR/obj" -name "*.class") \
      /sdk-cache/media3-jars/*.jar

    # 4. Add classes*.dex to APK
    cd "$BUILD_DIR/dex"
    aapt add "$BUILD_DIR/unaligned.apk" classes*.dex >/dev/null

    # 5. Zipalign
    zipalign -f 4 "$BUILD_DIR/unaligned.apk" "$BUILD_DIR/aligned.apk"

    # 6. Generate persistent release keystore and sign APK (v1 + v2)
    if [ ! -f /sdk-cache/release.keystore ]; then
      keytool -genkeypair -v \
        -keystore /sdk-cache/release.keystore \
        -alias tvcaster \
        -keyalg RSA -keysize 2048 -validity 10000 \
        -storepass tvcaster123 -keypass tvcaster123 \
        -dname "CN=TV Caster, OU=TV, O=TVCaster, L=Local, S=Local, C=US" >/dev/null 2>&1
    fi

    apksigner sign \
      --ks /sdk-cache/release.keystore \
      --ks-pass pass:tvcaster123 \
      --key-pass pass:tvcaster123 \
      --out /workspace/static/tv-caster-player.apk \
      "$BUILD_DIR/aligned.apk"

    apksigner verify /workspace/static/tv-caster-player.apk
  '

echo "Successfully built $OUT_APK ($(du -h "$OUT_APK" | cut -f1))"
