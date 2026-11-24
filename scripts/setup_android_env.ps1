param(
    [switch]$NonInteractive = $true,
    [switch]$SkipInstall = $false
)

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
$ToolsRoot = Join-Path $env:LOCALAPPDATA "Android\tools"
$DefaultSdkRoot = Join-Path $env:LOCALAPPDATA "Android\Sdk"
New-Item -ItemType Directory -Force -Path $ToolsRoot | Out-Null

$ManualCommands = @()

function Find-Jdk17 {
    $candidates = @()
    if ($env:JAVA_HOME) { $candidates += $env:JAVA_HOME }
    $javac = Get-Command javac -ErrorAction SilentlyContinue
    if ($javac) { $candidates += (Split-Path (Split-Path $javac.Source)) }
    foreach ($root in @(
        (Join-Path $ToolsRoot "jdk-17"),
        "C:\Program Files\Eclipse Adoptium",
        "C:\Program Files\Microsoft"
    )) {
        if (Test-Path $root) {
            if (Test-Path (Join-Path $root "bin\javac.exe")) { $candidates += $root }
            Get-ChildItem $root -Directory -ErrorAction SilentlyContinue | ForEach-Object {
                if (Test-Path (Join-Path $_.FullName "bin\javac.exe")) { $candidates += $_.FullName }
            }
        }
    }
    foreach ($c in ($candidates | Select-Object -Unique)) {
        $javaExe = Join-Path $c "bin\java.exe"
        if (Test-Path $javaExe) {
            $ver = & $javaExe -version 2>&1 | Out-String
            if ($ver -match '"17\.|"21\.') { return $c }
        }
    }
    return $null
}

function Find-Gradle {
    $g = Get-Command gradle -ErrorAction SilentlyContinue
    if ($g) { return (Split-Path (Split-Path $g.Source)) }
    $local = Join-Path $ToolsRoot "gradle-8.10.2"
    if (Test-Path (Join-Path $local "bin\gradle.bat")) { return $local }
    Get-ChildItem $ToolsRoot -Directory -Filter "gradle-*" -ErrorAction SilentlyContinue | ForEach-Object {
        if (Test-Path (Join-Path $_.FullName "bin\gradle.bat")) { return $_.FullName }
    }
    return $null
}

function Find-AndroidSdk {
    foreach ($p in @($env:ANDROID_HOME, $env:ANDROID_SDK_ROOT, $DefaultSdkRoot, "C:\Android\Sdk")) {
        if ($p -and (Test-Path $p)) { return $p }
    }
    return $null
}

function Find-SdkManager([string]$SdkRoot) {
    if (-not $SdkRoot) { return $null }
    foreach ($rel in @("cmdline-tools\latest\bin\sdkmanager.bat", "cmdline-tools\bin\sdkmanager.bat", "tools\bin\sdkmanager.bat")) {
        $full = Join-Path $SdkRoot $rel
        if (Test-Path $full) { return $full }
    }
    return $null
}

Write-Output "=== Mark-LVII Android Pre-Flight Check ==="

# 1. OpenJDK 17
$JdkHome = Find-Jdk17
if (-not $JdkHome -and -not $SkipInstall) {
    Write-Output "[Setup] Attempting OpenJDK 17 install via winget..."
    winget install --id EclipseAdoptium.Temurin.17.JDK --exact --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-Host
    $JdkHome = Find-Jdk17
    if (-not $JdkHome) {
        Write-Output "[Setup] winget JDK unavailable/blocked; downloading portable Temurin 17 ZIP..."
        $jdkZip = Join-Path $env:TEMP "temurin17-win-x64.zip"
        $jdkDest = Join-Path $ToolsRoot "jdk-17"
        try {
            Invoke-WebRequest -Uri "https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse" -OutFile $jdkZip -UseBasicParsing -TimeoutSec 180
            $extractTmp = Join-Path $ToolsRoot "_jdk_extract"
            if (Test-Path $extractTmp) { Remove-Item $extractTmp -Recurse -Force }
            Expand-Archive -Path $jdkZip -DestinationPath $extractTmp -Force
            $inner = Get-ChildItem $extractTmp -Directory | Select-Object -First 1
            if ($inner) {
                if (Test-Path $jdkDest) { Remove-Item $jdkDest -Recurse -Force }
                Move-Item $inner.FullName $jdkDest
                $JdkHome = Find-Jdk17
            }
            Remove-Item $extractTmp -Recurse -Force -ErrorAction SilentlyContinue
            Remove-Item $jdkZip -Force -ErrorAction SilentlyContinue
        } catch {
            Write-Output "[Warn] Portable JDK 17 download failed: $_"
            $ManualCommands += "winget install --id EclipseAdoptium.Temurin.17.JDK --exact"
        }
    }
}
if ($JdkHome) {
    $env:JAVA_HOME = $JdkHome
    $env:PATH = "$(Join-Path $JdkHome 'bin');$env:PATH"
    [Environment]::SetEnvironmentVariable("JAVA_HOME", $JdkHome, "User")
}

# 2. Gradle
$GradleHome = Find-Gradle
if (-not $GradleHome -and -not $SkipInstall) {
    Write-Output "[Setup] Attempting Gradle install via winget..."
    winget install --id Gradle.Gradle --exact --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-Host
    $GradleHome = Find-Gradle
    if (-not $GradleHome) {
        Write-Output "[Setup] winget Gradle unavailable/blocked; downloading portable Gradle 8.10.2..."
        $gZip = Join-Path $env:TEMP "gradle-8.10.2-bin.zip"
        try {
            Invoke-WebRequest -Uri "https://services.gradle.org/distributions/gradle-8.10.2-bin.zip" -OutFile $gZip -UseBasicParsing -TimeoutSec 180
            Expand-Archive -Path $gZip -DestinationPath $ToolsRoot -Force
            Remove-Item $gZip -Force -ErrorAction SilentlyContinue
            $GradleHome = Find-Gradle
        } catch {
            Write-Output "[Warn] Portable Gradle download failed: $_"
            $ManualCommands += "winget install --id Gradle.Gradle --exact"
        }
    }
}
if ($GradleHome) {
    $env:PATH = "$(Join-Path $GradleHome 'bin');$env:PATH"
}

# 3. Android SDK & Command-Line Tools
$SdkRoot = Find-AndroidSdk
if (-not $SdkRoot) { $SdkRoot = $DefaultSdkRoot }
New-Item -ItemType Directory -Force -Path $SdkRoot | Out-Null
$SdkManager = Find-SdkManager $SdkRoot
if (-not $SdkManager -and -not $SkipInstall) {
    Write-Output "[Setup] Installing Android Command-Line Tools into $SdkRoot..."
    $cliZip = Join-Path $env:TEMP "cmdline-tools-win.zip"
    $cmdRoot = Join-Path $SdkRoot "cmdline-tools"
    New-Item -ItemType Directory -Force -Path $cmdRoot | Out-Null
    try {
        Invoke-WebRequest -Uri "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip" -OutFile $cliZip -UseBasicParsing -TimeoutSec 180
        $tmpExt = Join-Path $env:TEMP "cmdline-tools-extract"
        if (Test-Path $tmpExt) { Remove-Item $tmpExt -Recurse -Force }
        Expand-Archive -Path $cliZip -DestinationPath $tmpExt -Force
        $latestDir = Join-Path $cmdRoot "latest"
        if (Test-Path $latestDir) { Remove-Item $latestDir -Recurse -Force }
        Move-Item (Join-Path $tmpExt "cmdline-tools") $latestDir
        Remove-Item $tmpExt -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item $cliZip -Force -ErrorAction SilentlyContinue
        $SdkManager = Find-SdkManager $SdkRoot
    } catch {
        Write-Output "[Warn] Android Command-Line Tools download failed: $_"
        $ManualCommands += "winget install --id Google.PlatformTools --exact"
    }
}

$env:ANDROID_HOME = $SdkRoot
$env:ANDROID_SDK_ROOT = $SdkRoot
[Environment]::SetEnvironmentVariable("ANDROID_HOME", $SdkRoot, "User")
[Environment]::SetEnvironmentVariable("ANDROID_SDK_ROOT", $SdkRoot, "User")

# 4. Licenses + Platform Tools / SDK 35
if ($SdkManager -and $JdkHome -and -not $SkipInstall) {
    Write-Output "[Setup] Accepting SDK licenses and verifying platform-tools, platforms;android-35, build-tools;35.0.0..."
    $needPkgs = @()
    if (-not (Test-Path (Join-Path $SdkRoot "platform-tools\adb.exe"))) { $needPkgs += "platform-tools" }
    if (-not (Test-Path (Join-Path $SdkRoot "platforms\android-35"))) { $needPkgs += "platforms;android-35" }
    if (-not (Test-Path (Join-Path $SdkRoot "build-tools\35.0.0"))) { $needPkgs += "build-tools;35.0.0" }
    if ($needPkgs.Count -gt 0) {
        try {
            $yes = ("y`n" * 25)
            $yes | & $SdkManager "--sdk_root=$SdkRoot" --licenses 2>&1 | Out-Null
            $yes | & $SdkManager "--sdk_root=$SdkRoot" @needPkgs 2>&1 | Out-Host
        } catch {
            Write-Output "[Warn] sdkmanager install warning: $_"
            $ManualCommands += "`"$SdkManager`" --sdk_root=`"$SdkRoot`" `"platform-tools`" `"platforms;android-35`" `"build-tools;35.0.0`""
        }
    }
}

$AdbPath = Join-Path $SdkRoot "platform-tools\adb.exe"
$Plat35 = Test-Path (Join-Path $SdkRoot "platforms\android-35")
$BuildTools35 = Test-Path (Join-Path $SdkRoot "build-tools\35.0.0")

Write-Output ""
Write-Output "=== Android Pre-Flight Summary ==="
Write-Output ("JDK17         : " + ($(if ($JdkHome) { $JdkHome } else { "MISSING" })))
Write-Output ("GRADLE        : " + ($(if ($GradleHome) { $GradleHome } else { "MISSING" })))
Write-Output ("ANDROID_SDK   : " + $SdkRoot)
Write-Output ("SDKMANAGER    : " + ($(if ($SdkManager) { $SdkManager } else { "MISSING" })))
Write-Output ("ADB           : " + ($(if (Test-Path $AdbPath) { $AdbPath } else { "MISSING" })))
Write-Output ("PLATFORM_35   : " + $Plat35)
Write-Output ("BUILD_TOOLS_35: " + $BuildTools35)

if ($ManualCommands.Count -gt 0) {
    Write-Output ""
    Write-Output "Manual Recovery Commands (if needed):"
    foreach ($mc in $ManualCommands) { Write-Output "  $mc" }
}
