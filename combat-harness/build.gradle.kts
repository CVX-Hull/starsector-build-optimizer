import net.ltgt.gradle.errorprone.errorprone

plugins {
    java
    id("net.ltgt.errorprone") version "5.1.0"
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

    // PINNED at 2.42.0 — do not bump: 2.43.0+ ship JDK-21 class files and
    // crash the JDK-17 compiler JVM this project builds with.
    errorprone("com.google.errorprone:error_prone_core:2.42.0")
    errorprone("com.uber.nullaway:nullaway:0.13.7")
    compileOnly("org.jspecify:jspecify:1.0.0")

    testImplementation("org.junit.jupiter:junit-jupiter:5.10.2")
    testImplementation(files("${gameDir}/json.jar"))
    testImplementation(files("${gameDir}/starfarer.api.jar"))
    testImplementation(files("${gameDir}/log4j-1.2.9.jar"))
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

tasks.withType<JavaCompile>().configureEach {
    options.compilerArgs.addAll(listOf("-Xlint:all", "-Werror"))
    options.errorprone {
        // Deliberate catch(Throwable) wrappers around obfuscated game API
        // calls are a documented project pattern (see starsector-modding skill).
        disable("EmptyCatch")
        error("NullAway")
        option("NullAway:AnnotatedPackages", "starsector.combatharness,data.missions")
        // BaseEveryFrameCombatPlugin.init(CombatEngineAPI) is the engine-driven
        // lifecycle initializer for combat plugins (verified against the API jar).
        option(
            "NullAway:KnownInitializers",
            "com.fs.starfarer.api.combat.BaseEveryFrameCombatPlugin.init",
        )
    }
}

// Tests intentionally pass null literals to exercise null-tolerant defaults
// (e.g. ManifestDumperTest null-handling cases). The current suite compiles
// clean under NullAway after the main-code @Nullable contracts were added,
// but the intentional-null test pattern stays legitimate, so NullAway is
// scoped to production sources only. Characterization: all 14 findings at
// adoption time were this pattern (2026-07-12).
tasks.named<JavaCompile>("compileTestJava") {
    options.errorprone.disable("NullAway")
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
