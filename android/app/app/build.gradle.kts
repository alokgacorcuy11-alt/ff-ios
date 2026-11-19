plugins {
    id("com.android.application")
}

// Identity is a build input, not a constant. The game can only blacklist what
// it can name: a package id, a signing certificate, an app label. Every CI
// build gets a fresh random one (see .github/workflows/android.yml), so a
// package that ends up on a list is retired with the build that carried it.
// Local builds keep the defaults, which is what the docs and adb commands use.
val appId = (findProperty("appId") as String?) ?: "com.alokg.ffmenu"
val appLabel = (findProperty("appLabel") as String?) ?: "FFMenu"

android {
    namespace = "com.alokg.ffmenu"
    compileSdk = 34

    defaultConfig {
        applicationId = appId
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
        resValue("string", "app_name", appLabel)
    }

    signingConfigs {
        getByName("debug") {
            val ks = findProperty("ksFile") as String?
            if (ks != null) {
                storeFile = file(ks)
                storePassword = findProperty("ksPass") as String?
                keyAlias = findProperty("ksAlias") as String?
                keyPassword = findProperty("ksKeyPass") as String?
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    packaging {
        // the daemon is an asset we extract and run with root, not a JNI lib
        resources.excludes += setOf("META-INF/*")
    }
}

// MainActivity has a non-ASCII label ("FF · mem bridge"); pin the encoding so
// the build does not depend on the platform default.
tasks.withType<org.gradle.api.tasks.compile.JavaCompile>().configureEach {
    options.encoding = "UTF-8"
}
