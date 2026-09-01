$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$pythonw = Join-Path $projectRoot ".venv\Scripts\pythonw.exe"
$logPath = Join-Path $projectRoot "听译记-启动日志.log"

try {
    Set-Location -LiteralPath $projectRoot
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 正在启动听译记" |
        Set-Content -LiteralPath $logPath -Encoding UTF8

    if (-not (Test-Path -LiteralPath $python)) {
        throw "Python virtual environment was not found: $python"
    }
    if (-not (Test-Path -LiteralPath $pythonw)) {
        throw "Python windowed executable was not found: $pythonw"
    }

    $checkOutput = & $python -c "import classnote.qt_gui; print('TingYi Notes preflight OK')" 2>&1
    $checkOutput | Add-Content -LiteralPath $logPath -Encoding UTF8
    if ($LASTEXITCODE -ne 0) {
        throw "听译记启动检查失败，请查看：$logPath"
    }

    $process = Start-Process `
        -FilePath $pythonw `
        -ArgumentList "-m", "classnote.qt_gui" `
        -WorkingDirectory $projectRoot `
        -WindowStyle Normal `
        -PassThru

    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Started PID $($process.Id)" |
        Add-Content -LiteralPath $logPath -Encoding UTF8
}
catch {
    $message = "听译记无法启动。`n`n$($_.Exception.Message)`n`n诊断日志：$logPath"
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] ERROR: $($_.Exception.ToString())" |
        Add-Content -LiteralPath $logPath -Encoding UTF8
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        $message,
        "听译记启动失败",
        [System.Windows.MessageBoxButton]::OK,
        [System.Windows.MessageBoxImage]::Error
    ) | Out-Null
    exit 1
}
