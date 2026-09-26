param(
    [Parameter(Mandatory = $true)][string]$Skill,
    [Parameter(Mandatory = $true)][string]$Catalog,
    [Parameter(Mandatory = $true)][string]$Phase,
    [Parameter(Mandatory = $true)][string]$FilePath,
    [string[]]$ArgumentList = @()
)

$start = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() * 1000000
$exitCode = 1
try {
    & $FilePath @ArgumentList
    $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }
}
finally {
    $ended = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() * 1000000
    try {
        $endpoint = if ($env:SKILL_OBSERVER_PHASE_ENDPOINT) {
            [Uri]$env:SKILL_OBSERVER_PHASE_ENDPOINT
        } else {
            [Uri]'http://127.0.0.1:4318/v1/phase-batches'
        }
        if ($endpoint.Scheme -eq 'http' -and $endpoint.Host -in @('127.0.0.1', 'localhost')) {
            $payload = @{
                skill_name = $Skill
                thread_key = $env:SKILL_OBSERVER_THREAD_ID
                catalog_version = $Catalog
                script_sha256 = $null
                intervals = @(@{
                    phase_code = $Phase
                    started_at_ns = $start
                    ended_at_ns = [Math]::Max($start, $ended)
                    fields = @{ operation = [IO.Path]::GetFileNameWithoutExtension($FilePath) }
                })
            } | ConvertTo-Json -Depth 6 -Compress
            $raw = [Text.Encoding]::UTF8.GetBytes($payload)
            $stream = [IO.MemoryStream]::new()
            $gzip = [IO.Compression.GZipStream]::new($stream, [IO.Compression.CompressionLevel]::Fastest, $true)
            $gzip.Write($raw, 0, $raw.Length)
            $gzip.Dispose()
            $client = [Net.Http.HttpClient]::new()
            $client.Timeout = [TimeSpan]::FromMilliseconds(100)
            $content = [Net.Http.ByteArrayContent]::new($stream.ToArray())
            $content.Headers.ContentType = [Net.Http.Headers.MediaTypeHeaderValue]::new('application/json')
            $content.Headers.ContentEncoding.Add('gzip')
            $null = $client.PostAsync($endpoint, $content).GetAwaiter().GetResult()
            $content.Dispose()
            $client.Dispose()
            $stream.Dispose()
        }
    } catch {}
}
exit $exitCode
