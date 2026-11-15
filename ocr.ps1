param([Parameter(Mandatory=$true)][string]$ImagePath)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[void][Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
[void][Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
[void][Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
[void][Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
function Await-Result($op, $type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    try { $task.Wait() } catch { throw ($task.Exception.ToString()) }
    return $task.Result
}
$file = Await-Result ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
$stream = Await-Result ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await-Result ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await-Result ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$language = [Windows.Globalization.Language]::new('zh-Hans-CN')
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
if (!$engine) { throw 'Windows Simplified Chinese OCR language is unavailable.' }
$result = Await-Result ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$lines = @($result.Lines | ForEach-Object {
    $words = @($_.Words)
    if ($words.Count) {
        $left = ($words | ForEach-Object {$_.BoundingRect.X} | Measure-Object -Minimum).Minimum
        $top = ($words | ForEach-Object {$_.BoundingRect.Y} | Measure-Object -Minimum).Minimum
        $right = ($words | ForEach-Object {$_.BoundingRect.X + $_.BoundingRect.Width} | Measure-Object -Maximum).Maximum
        $bottom = ($words | ForEach-Object {$_.BoundingRect.Y + $_.BoundingRect.Height} | Measure-Object -Maximum).Maximum
        [pscustomobject]@{text=$_.Text; x=$left; y=$top; w=($right-$left); h=($bottom-$top)}
    }
})
@{lines=$lines} | ConvertTo-Json -Depth 5 -Compress
$stream.Dispose()
