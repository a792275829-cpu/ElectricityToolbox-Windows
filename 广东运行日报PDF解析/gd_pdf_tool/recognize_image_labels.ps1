param([Parameter(Mandatory=$true)][string]$ImagePath)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime]
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
function Await-WinRT($Operation, [Type]$ResultType) {
    $task = $asTask.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    $task.GetAwaiter().GetResult()
}
$file = Await-WinRT ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
$stream = Await-WinRT ($file.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
try {
    $decoder = Await-WinRT ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await-WinRT ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    try {
        $language = [Windows.Globalization.Language]::new('zh-Hans')
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
        if ($null -eq $engine) { throw 'Install the Windows Simplified Chinese OCR language feature to recognize report image labels.' }
        $result = Await-WinRT ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        $labels = @()
        foreach ($line in $result.Lines) {
            if ($line.Text -notmatch '[\u4e00-\u9fff]') { continue }
            $left = [double]::PositiveInfinity
            $top = [double]::PositiveInfinity
            $right = 0.0
            $bottom = 0.0
            foreach ($word in $line.Words) {
                $box = $word.BoundingRect
                $left = [Math]::Min($left, $box.X)
                $top = [Math]::Min($top, $box.Y)
                $right = [Math]::Max($right, $box.X + $box.Width)
                $bottom = [Math]::Max($bottom, $box.Y + $box.Height)
            }
            $labels += @{
                text = $line.Text
                box = @{
                    origin = @{ x = $left / $bitmap.PixelWidth; y = 1 - $bottom / $bitmap.PixelHeight }
                    size = @{ width = ($right - $left) / $bitmap.PixelWidth; height = ($bottom - $top) / $bitmap.PixelHeight }
                }
            }
        }
        ConvertTo-Json -InputObject @($labels) -Depth 5 -Compress
    } finally { $bitmap.Dispose() }
} finally { $stream.Dispose() }
