plugins {
    kotlin("jvm") version "2.1.20"
}

repositories {
    mavenCentral()
}

dependencies {
    compileOnly("io.gitlab.arturbosch.detekt:detekt-api:1.23.8")
    testImplementation(kotlin("test"))
    testImplementation("io.gitlab.arturbosch.detekt:detekt-test:1.23.8")
}

kotlin {
    jvmToolchain(17)
}
