plugins {
    id("com.android.application")
}

// The root tier, as its own app.
//
// Why not a screen inside the no-root product: that app is sold to people who
// will never root, and adding a root checker, a su caller and a per-device
// flashing guide to it would put all of that in the dex of every APK they
// install - for a feature they do not use. Two audiences, two APKs.
//
// This one asks for no permissions at all. Every privileged action goes
// through `su -c`, which the user grants once and can revoke in the superuser
// app. There is no service, no receiver, no native code, nothing that runs
// when nobody asked.

val appId = (findProperty("rootAppId") as String?) ?: "com.alokg.roottools"
val appLabel = (findProperty("rootAppLabel") as String?) ?: "Root Tools"

android {
    namespace = "com.alokg.roottools"
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
                storeType = "JKS"
                storePassword = findProperty("ksPass") as String?
                keyAlias = findProperty("ksAlias") as String?
                keyPassword = findProperty("ksKeyPass") as String?
                enableV1Signing = true
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
        resources.excludes += setOf("META-INF/*")
    }
}

tasks.withType<org.gradle.api.tasks.compile.JavaCompile>().configureEach {
    options.encoding = "UTF-8"
}