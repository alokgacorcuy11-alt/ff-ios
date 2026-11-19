plugins {
    id("com.android.application")
}

android {
    namespace = "com.alokg.ffmenu"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.alokg.ffmenu"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
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
