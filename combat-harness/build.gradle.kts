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
    compileOnly(files("${gameDir}/lwjgl_util.jar"))

    // Use game's json.jar for compilation (older org.json with checked exceptions)
    compileOnly(files("${gameDir}/json.jar"))

    testImplementation("org.junit.jupiter:junit-jupiter:5.10.2")
    testImplementation(files("${gameDir}/json.jar"))
    testImplementation(files("${gameDir}/starfarer.api.jar"))
    testImplementation(files("${gameDir}/log4j-1.2.9.jar"))
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

tasks.test {
    useJUnitPlatform()
}

// Generate a build-info properties file with the current git SHA so
// ManifestDumper can embed it in the emitted manifest. This is the sole
// source of truth for `manifest.constants.mod_commit_sha`; Packer then
// reads that value back out of the manifest JSON to tag the AMI. Every
// link — jar → manifest → AMI tag → preflight dual-check — points back
// to this file, so they cannot desync.
val buildInfoDir = layout.buildDirectory.dir("generated/build-info")
val repoRoot = rootProject.projectDir.resolve("..")
val generateBuildInfo by tasks.registering {
    val outputFile = buildInfoDir.map { it.file("combat-harness-build-info.properties") }
    outputs.file(outputFile)
    // Always re-run — git HEAD can change between otherwise-identical gradle
    // invocations, and each jar must carry its true build-time SHA.
    outputs.upToDateWhen { false }
    val captured = repoRoot
    doLast {
        val process = ProcessBuilder("git", "rev-parse", "HEAD")
            .directory(captured)
            .redirectErrorStream(true)
            .start()
        val output = process.inputStream.bufferedReader().use { it.readText().trim() }
        val exit = process.waitFor()
        if (exit != 0 || output.isEmpty()) {
            throw GradleException(
                "`git rev-parse HEAD` failed (exit=$exit, output=${'"'}$output${'"'}) " +
                "in $captured. The combat-harness jar refuses to build outside a " +
                "git checkout because the manifest it produces must carry a " +
                "verifiable mod_commit_sha (preflight dual-check). If building " +
                "from a tarball release, populate build/generated/build-info/" +
                "combat-harness-build-info.properties manually and skip this task."
            )
        }
        val file = outputFile.get().asFile
        file.parentFile.mkdirs()
        file.writeText("gitSha=$output\n", Charsets.UTF_8)
    }
}

sourceSets {
    main {
        resources {
            srcDir(buildInfoDir)
        }
    }
}

tasks.named("processResources") {
    dependsOn(generateBuildInfo)
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
