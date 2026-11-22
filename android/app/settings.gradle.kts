pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}
rootProject.name = "ffmenu"
include(":app")

// The root tier ships as its own APK: the buyers who will never root should not
// carry a root checker and a su caller in the dex of the app they install.
include(":rootapp")
