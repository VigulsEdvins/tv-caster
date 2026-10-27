#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT_DIR/android-tv/app/src/main"
OUT_APK="$ROOT_DIR/static/tv-caster-player.apk"

mkdir -p "$ROOT_DIR/static" /tmp/android-sdk-cache

if [ ! -f /tmp/android-sdk-cache/android.jar ]; then
  echo "Downloading Android 28 platform android.jar..."
  curl -sL https://dl.google.com/android/repository/platform-28_r06.zip -o /tmp/android-sdk-cache/platform.zip
  python3 -c "import zipfile; z=zipfile.ZipFile('/tmp/android-sdk-cache/platform.zip'); open('/tmp/android-sdk-cache/android.jar','wb').write(z.read('android-9/android.jar'))"
  rm -f /tmp/android-sdk-cache/platform.zip
fi

if [ ! -f /tmp/android-sdk-cache/d8.jar ]; then
  echo "Downloading Android build-tools 34 d8.jar..."
  curl -sL https://dl.google.com/android/repository/build-tools_r34-linux.zip -o /tmp/android-sdk-cache/bt.zip
  python3 -c "import zipfile; z=zipfile.ZipFile('/tmp/android-sdk-cache/bt.zip'); open('/tmp/android-sdk-cache/d8.jar','wb').write(z.read('android-14/lib/d8.jar'))"
  rm -f /tmp/android-sdk-cache/bt.zip
fi

echo "Building and signing tv-caster-player.apk in Docker..."
docker run --rm --entrypoint bash \
  -v "$ROOT_DIR:/workspace" \
  -v "/tmp/android-sdk-cache:/sdk-cache" \
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

    # 2. Compile Java sources
    javac -source 1.8 -target 1.8 \
      -bootclasspath /sdk-cache/android.jar \
      -classpath /sdk-cache/android.jar \
      -d "$BUILD_DIR/obj" \
      "$BUILD_DIR/gen/com/tvcaster/player/R.java" \
      /workspace/android-tv/app/src/main/java/com/tvcaster/player/MainActivity.java

    # 3. Convert .class files to classes.dex using d8
    java -cp /sdk-cache/d8.jar com.android.tools.r8.D8 \
      --lib /sdk-cache/android.jar \
      --min-api 21 \
      --output "$BUILD_DIR/dex" \
      $(find "$BUILD_DIR/obj" -name "*.class")

    # 4. Add classes.dex to APK
    cd "$BUILD_DIR/dex"
    aapt add "$BUILD_DIR/unaligned.apk" classes.dex >/dev/null

    # 5. Zipalign
    zipalign -f 4 "$BUILD_DIR/unaligned.apk" "$BUILD_DIR/aligned.apk"

    # 6. Generate debug/release keystore and sign APK (v1 + v2)
    keytool -genkeypair -v \
      -keystore "$BUILD_DIR/release.keystore" \
      -alias tvcaster \
      -keyalg RSA -keysize 2048 -validity 10000 \
      -storepass tvcaster123 -keypass tvcaster123 \
      -dname "CN=TV Caster, OU=TV, O=TVCaster, L=Local, S=Local, C=US" >/dev/null 2>&1

    apksigner sign \
      --ks "$BUILD_DIR/release.keystore" \
      --ks-pass pass:tvcaster123 \
      --key-pass pass:tvcaster123 \
      --out /workspace/static/tv-caster-player.apk \
      "$BUILD_DIR/aligned.apk"

    apksigner verify /workspace/static/tv-caster-player.apk
  '

echo "Successfully built $OUT_APK ($(du -h "$OUT_APK" | cut -f1))"
