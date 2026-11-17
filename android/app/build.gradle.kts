plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.starlightinfinite.phonemic"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.starlightinfinite.phonemic"
        minSdk = 24
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        getByName("release") {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    // 扫码兜底：外网 Cloudflare 模式拿到的是临时域名，自动发现只在 WLAN 内有效，
    // 此时扫 PC 主界面的二维码最快。ZXing 此封装不依赖 Google Play 服务。
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")
}
