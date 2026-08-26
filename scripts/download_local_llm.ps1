param(
    [string]$Destination = "E:\yjd\video\video\models\llm\Qwen.Qwen3.5-9B.Q4_K_M.gguf",
    [int]$Connections = 8,
    [string]$SourceUrl = "https://hf-mirror.com/DevQuasar/Qwen.Qwen3.5-9B-GGUF/resolve/main/Qwen.Qwen3.5-9B.Q4_K_M.gguf"
)

$ErrorActionPreference = "Stop"
$expectedBytes = [int64]5627044704
$destinationPath = [System.IO.Path]::GetFullPath($Destination)
$destinationDirectory = [System.IO.Path]::GetDirectoryName($destinationPath)
$partsDirectory = Join-Path $destinationDirectory ".qwen35_parts"
$assembledPath = "$destinationPath.assembling"

New-Item -ItemType Directory -Force -Path $destinationDirectory | Out-Null
New-Item -ItemType Directory -Force -Path $partsDirectory | Out-Null

$chunkSize = [int64][Math]::Ceiling($expectedBytes / $Connections)
$downloads = @()
for ($index = 0; $index -lt $Connections; $index++) {
    $start = [int64]$index * $chunkSize
    $end = [Math]::Min($expectedBytes - 1, $start + $chunkSize - 1)
    $expectedPartBytes = $end - $start + 1
    $partPath = Join-Path $partsDirectory ("part_{0:D2}.bin" -f $index)
    if ((Test-Path -LiteralPath $partPath) -and
        (Get-Item -LiteralPath $partPath).Length -eq $expectedPartBytes) {
        continue
    }
    if (Test-Path -LiteralPath $partPath) {
        Remove-Item -LiteralPath $partPath -Force
    }
    $arguments = @(
        "-L",
        "--fail",
        "--retry", "8",
        "--retry-delay", "3",
        "--connect-timeout", "20",
        "--speed-limit", "1024",
        "--speed-time", "30",
        "--range", "$start-$end",
        "--url", $SourceUrl,
        "--output", $partPath
    )
    $process = Start-Process -FilePath "curl.exe" -ArgumentList $arguments `
        -WindowStyle Hidden -PassThru
    $downloads += [pscustomobject]@{
        Process = $process
        PartPath = $partPath
        ExpectedBytes = $expectedPartBytes
    }
}

foreach ($download in $downloads) {
    $download.Process.WaitForExit()
    if ($download.Process.ExitCode -ne 0) {
        throw "分段下载失败，curl 退出码 $($download.Process.ExitCode): $($download.PartPath)"
    }
    $actualBytes = (Get-Item -LiteralPath $download.PartPath).Length
    if ($actualBytes -ne $download.ExpectedBytes) {
        throw "分段大小错误: $($download.PartPath), $actualBytes / $($download.ExpectedBytes)"
    }
}

if (Test-Path -LiteralPath $assembledPath) {
    Remove-Item -LiteralPath $assembledPath -Force
}
$output = [System.IO.File]::Open(
    $assembledPath,
    [System.IO.FileMode]::CreateNew,
    [System.IO.FileAccess]::Write,
    [System.IO.FileShare]::None
)
try {
    for ($index = 0; $index -lt $Connections; $index++) {
        $partPath = Join-Path $partsDirectory ("part_{0:D2}.bin" -f $index)
        $input = [System.IO.File]::OpenRead($partPath)
        try {
            $input.CopyTo($output)
        }
        finally {
            $input.Dispose()
        }
    }
}
finally {
    $output.Dispose()
}

$assembledBytes = (Get-Item -LiteralPath $assembledPath).Length
if ($assembledBytes -ne $expectedBytes) {
    throw "合并后模型大小错误: $assembledBytes / $expectedBytes"
}

if (Test-Path -LiteralPath $destinationPath) {
    Move-Item -LiteralPath $destinationPath -Destination "$destinationPath.single.partial" -Force
}
Move-Item -LiteralPath $assembledPath -Destination $destinationPath

Write-Output "MODEL_READY=$destinationPath"
Write-Output "MODEL_BYTES=$assembledBytes"
