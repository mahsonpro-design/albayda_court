plugins {
    id("com.android.application") version "8.7.3"
    id("org.jetbrains.kotlin.android") version "2.0.21"
    id("org.jetbrains.kotlin.plugin.compose") version "2.0.21"
}

android {
    namespace = "org.albayda.court"
    compileSdk = 35

    defaultConfig {
        applicationId = "org.albayda.court"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
        val apiBase = (project.findProperty("API_BASE_URL") as String?) ?: "https://api.example.invalid/"
        buildConfigField("String", "API_BASE_URL", "\"$apiBase\"")
    }
    buildFeatures { compose = true; buildConfig = true }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.12.01"))
    implementation("androidx.activity:activity-compose:1.10.0")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.foundation:foundation")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    debugImplementation("androidx.compose.ui:ui-tooling")
}
