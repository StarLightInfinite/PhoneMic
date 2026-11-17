// 根构建脚本：只声明插件版本，不应用。
// 本机无需 Android SDK —— CI（.github/workflows/android-build.yml）负责构建。
plugins {
    id("com.android.application") version "8.5.2" apply false
    id("org.jetbrains.kotlin.android") version "2.0.21" apply false
}
