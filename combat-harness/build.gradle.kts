plugins {
    java
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

repositories {
    mavenCentral()
}

val gameDir = rootProject.projectDir.resolve("../game/starsector")

dependencies {
    compileOnly(files("${gameDir}/starfarer.api.jar"))
    compileOnly(files("${gameDir}/log4j-1.2.9.jar"))

    // Use game's json.jar for compilation (older org.json with checked exceptions)
    compileOnly(files("${gameDir}/json.jar"))

    testImplementation("org.junit.jupiter:junit-jupiter:5.10.2")
    testImplementation(files("${gameDir}/json.jar"))
    testImplementation(files("${gameDir}/starfarer.api.jar"))
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

tasks.test {
    useJUnitPlatform()
}

tasks.jar {
    archiveFileName.set("combat-harness.jar")
    destinationDirectory.set(file("mod/jars"))
}

tasks.register<Copy>("deploy") {
    dependsOn("jar")
    from("mod")
    into("${gameDir}/mods/combat-harness")
}
