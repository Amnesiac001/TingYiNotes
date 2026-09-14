$ErrorActionPreference = "Stop"

$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
$releaseRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot "release"))
$archive = [IO.Path]::GetFullPath((Join-Path $releaseRoot "TingYiNotes-0.2.1-Windows.zip"))
$releasePrefix = $releaseRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $archive.StartsWith($releasePrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Release archive escaped the release directory: $archive"
}

$pyinstaller = Join-Path $projectRoot ".venv\Scripts\pyinstaller.exe"
if (-not (Test-Path -LiteralPath $pyinstaller)) {
    throw 'Missing PyInstaller. Run: .\.venv\Scripts\python.exe -m pip install -e ".[windows-build]"'
}

Push-Location $projectRoot
$originalPath = $env:PATH
try {
    # Do not let unrelated developer tools on PATH contribute conflicting DLLs.
    $env:PATH = @((Split-Path -Parent $pyinstaller), (Join-Path $env:SystemRoot "System32"), $env:SystemRoot) -join ";"
    & $pyinstaller --noconfirm "windows/TingYiNotes.spec"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed: $LASTEXITCODE" }

    $bundle = Join-Path $projectRoot "dist\TingYiNotes-Windows"
    $executable = Join-Path $bundle "TingYiNotes.exe"
    if (-not (Test-Path -LiteralPath $executable)) { throw "Missing executable: $executable" }
    foreach ($library in @("cublas64_12.dll", "cudnn64_9.dll")) {
        if (-not (Get-ChildItem -LiteralPath $bundle -Recurse -File -Filter $library)) {
            throw "Missing CUDA library: $library"
        }
    }
    if (Get-ChildItem -LiteralPath $bundle -Recurse -File -Filter "icuuc.dll") {
        throw "A conflicting ICU library entered the Windows bundle."
    }
    $privateFiles = @(Get-ChildItem -LiteralPath $bundle -Recurse -File -Force |
        Where-Object { $_.Name -eq ".env" -or $_.Extension -in @(".db", ".log", ".wav", ".mp3", ".mp4") })
    if ($privateFiles.Count -gt 0) {
        throw "Private data entered the Windows bundle: $($privateFiles[0].FullName)"
    }

    # Launch the frozen EXE itself in an isolated profile before archiving it.
    $checkRoot = Join-Path $projectRoot ("build\TingYiNotes-check-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $checkRoot | Out-Null
    $modelCache = Join-Path $projectRoot "data\models\models--Systran--faster-distil-whisper-large-v3\snapshots"
    $modelSnapshot = $null
    if (Test-Path -LiteralPath $modelCache) {
        $modelSnapshot = Get-ChildItem -LiteralPath $modelCache -Directory |
            Where-Object {
                (Test-Path -LiteralPath (Join-Path $_.FullName "config.json")) -and
                (Test-Path -LiteralPath (Join-Path $_.FullName "model.bin")) -and
                (Test-Path -LiteralPath (Join-Path $_.FullName "tokenizer.json"))
            } | Select-Object -First 1
    }
    $isolatedVariables = @("APPDATA", "CLASSNOTE_ENV_FILE", "CLASSNOTE_DB", "CLASSNOTE_EXPORT_DIR", "TINGYI_SELF_CHECK_MODEL_PATH")
    $originalValues = @{}
    foreach ($name in $isolatedVariables) {
        $originalValues[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
    }
    try {
        $env:APPDATA = $checkRoot
        foreach ($name in $isolatedVariables | Where-Object { $_ -ne "APPDATA" }) {
            Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        }
        if ($modelSnapshot) {
            $env:TINGYI_SELF_CHECK_MODEL_PATH = $modelSnapshot.FullName
        }
        $checkProcess = Start-Process -FilePath $executable -ArgumentList "--self-check" -PassThru -WindowStyle Hidden
        if (-not $checkProcess.WaitForExit(180000)) {
            Stop-Process -Id $checkProcess.Id
            throw "Bundled self-check timed out. Inspect: $checkRoot"
        }
        $reportFile = Get-ChildItem -LiteralPath $checkRoot -Recurse -File -Filter "self-check.json" |
            Select-Object -First 1
        if ($checkProcess.ExitCode -ne 0 -or -not $reportFile) {
            throw "Bundled self-check failed. Inspect: $checkRoot"
        }
        $report = Get-Content -LiteralPath $reportFile.FullName -Raw | ConvertFrom-Json
        if (-not $report.qt_version -or -not $report.storage_ready) {
            throw "Bundled self-check reported incomplete startup. Inspect: $reportFile"
        }
        if ($modelSnapshot -and -not $report.model_inference_ready) {
            throw "Bundled offline model inference failed. Inspect: $reportFile"
        }
        $demoProcess = Start-Process -FilePath $executable -ArgumentList "--release-smoke" -PassThru -WindowStyle Hidden
        if (-not $demoProcess.WaitForExit(180000)) {
            Stop-Process -Id $demoProcess.Id
            throw "Bundled offline demo timed out. Inspect: $checkRoot"
        }
        $demoReportFile = Get-ChildItem -LiteralPath $checkRoot -Recurse -File -Filter "release-smoke.json" |
            Select-Object -First 1
        if ($demoProcess.ExitCode -ne 0 -or -not $demoReportFile) {
            throw "Bundled offline demo failed. Inspect: $checkRoot"
        }
        $demoReport = Get-Content -LiteralPath $demoReportFile.FullName -Raw | ConvertFrom-Json
        if (-not $demoReport.demo_course_saved -or -not $demoReport.demo_export_ready -or
            $demoReport.demo_segments -ne 2) {
            throw "Bundled offline demo reported incomplete output. Inspect: $demoReportFile"
        }
        Write-Host "Bundled self-check: Qt $($report.qt_version), CUDA devices $($report.cuda_devices), audio inputs $($report.input_devices), model inference $($report.model_inference_ready)"
        Write-Host "Bundled offline demo: course saved and Markdown exported"
    }
    finally {
        foreach ($name in $isolatedVariables) {
            if ($null -eq $originalValues[$name]) {
                Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
            } else {
                [Environment]::SetEnvironmentVariable($name, $originalValues[$name], "Process")
            }
        }
    }

    Copy-Item -LiteralPath (Join-Path $projectRoot "LICENSE") -Destination $bundle
    New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
    if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive -Force }
    Compress-Archive -LiteralPath $bundle -DestinationPath $archive -CompressionLevel Optimal
    Write-Host "Windows release archive created: $archive"
    Write-Host "Test startup, microphone, RTX recognition and export on a clean Windows PC before publishing."
}
finally {
    $env:PATH = $originalPath
    Pop-Location
}
