$ErrorActionPreference = 'Stop'
$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$goldPath = Join-Path $base 'gold_keywords.json'
$docDir = 'C:\Users\guhao\PyCharmMiscProject\igf_classified_20260812_060303\_invalid\other'
$outDir = Join-Path $base 'results_kw'
New-Item -ItemType Directory -Path $outDir -Force | Out-Null
$gold = Get-Content -LiteralPath $goldPath -Raw -Encoding UTF8 | ConvertFrom-Json

function Get-Window([string]$file) {
    $raw = [IO.File]::ReadAllText((Join-Path $docDir $file), [Text.Encoding]::UTF8)
    $t = $raw -replace '(?is)<script.*?</script>', ' ' -replace '(?is)<style.*?</style>', ' ' -replace '<[^>]+>', ' '
    $t = $t -replace '&amp;', '&' -replace '&gt;', '>' -replace '&lt;', '<' -replace '&quot;', '"' -replace '&#\d+;', ' '
    $t = ($t -replace '\s+', ' ').Trim()
    if ($t.Length -gt 4000) { $t.Substring(0, 4000) } else { $t }
}

$intro = "You are extracting structured information from a verbatim transcript of an Internet Governance Forum (IGF) meeting.`nRead the excerpt below and extract the 8 to 15 most important keywords and key phrases.`nRules: phrases must be short (1-4 words); prefer phrases that appear verbatim in the text; cover topics, issues, actors and outcomes; do not invent.`nReturn ONLY strict JSON with this shape:`n{""keywords"": [""kw1"", ""kw2"", ""...""]}`n`nExcerpt:`n"
$example = "Example:`nExcerpt: ""IGF 2 Rio de Janeiro, Brazil 13 November 2007 Access >>HELIO COSTA: What makes the IGF a different forum is the fact that here, the forum is open to all. Even though there have been significant efforts by governments and companies to reduce the digital gap, differences still persist in access to information between developed and developing countries and between the rich and the poor. We are here to try and find solutions for the infrastructure, legal, and regulatory bottlenecks.""`nExample output:`n{""keywords"": [""access"", ""digital gap"", ""developed and developing countries"", ""rich and the poor"", ""infrastructure bottlenecks"", ""regulatory bottlenecks"", ""open to all""]}`n`nNow do the same for this excerpt:`n"

function Invoke-Ollama([string]$model, [string]$prompt) {
    $np = 400; if ($model -eq 'qwen3.5:9b') { $np = 2000 }
    $opts = @{ temperature = 0; num_predict = $np }
    if ($model -like 'qwen3*') { $opts['think'] = $false }
    $body = @{ model = $model; prompt = $prompt; stream = $false; format = 'json'; options = $opts } | ConvertTo-Json -Depth 5 -Compress
    Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/generate' -Method Post -Body $body -ContentType 'application/json; charset=utf-8' -TimeoutSec 240
}

$runs = @()
$plan = @(
    @{m='qwen3:8b';      md='oneshot'}, @{m='qwen3:8b';      md='fewshot'},
    @{m='qwen2.5:latest'; md='oneshot'}, @{m='qwen2.5:latest'; md='fewshot'},
    @{m='qwen3.5:9b';    md='oneshot'}
)
foreach ($p in $plan) {
    foreach ($g in $gold) {
        $window = Get-Window $g.file
        if ($p.md -eq 'fewshot') { $prompt = $intro + $example + $window } else { $prompt = $intro + $window }
        $t0 = Get-Date
        $kw = @(); $parsed = $false; $err = $null; $src = 'none'
        try {
            $resp = Invoke-Ollama $p.m $prompt
            $raw = [string]$resp.response; $src = 'response'
            if ([string]::IsNullOrWhiteSpace($raw) -and $resp.thinking) {
                $raw = [string]$resp.thinking; $src = 'thinking_salvage'
                $m2 = [regex]::Matches($raw, '(?s)\{\s*"keywords"\s*:\s*\[[^\]]*\]\s*\}')
                if ($m2.Count -gt 0) { $raw = $m2[$m2.Count - 1].Value } else { $raw = '' }
            }
            try {
                $obj = $raw | ConvertFrom-Json
                if ($obj.keywords -is [System.Array]) {
                    $kw = @($obj.keywords | ForEach-Object { [string]$_ })
                } elseif ($obj.keywords -is [string]) {
                    $kw = @([string]$obj.keywords -split '[,\n;]' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
                }
                $parsed = $kw.Count -gt 0
            } catch {
                $m = [regex]::Match($raw, '(?s)\[.*\]')
                if ($m.Success) {
                    $kw = @([regex]::Matches($m.Value, '"([^"]+)"') | ForEach-Object { $_.Groups[1].Value })
                    $parsed = $kw.Count -gt 0
                }
            }
        } catch { $err = $_.Exception.Message.Substring(0, [Math]::Min(180, $_.Exception.Message.Length)) }
        $runs += [pscustomobject]@{ model=$p.m; method=$p.md; doc=$g.doc; keywords=$kw; parsed=$parsed; source=$src;
                                   latency_s=[math]::Round(((Get-Date)-$t0).TotalSeconds,1); error=$err }
        $lat = [math]::Round(((Get-Date)-$t0).TotalSeconds,1)
        Write-Host ("[{0}/{1}] {2,-28} n={3} {4}s {5}" -f $p.m,$p.md,$g.doc,$kw.Count,$lat,($(if($parsed){'ok'}elseif($err){'ERR'}else{'fail'})))
        Start-Sleep -Seconds 1
    }
}
$out = @{ gold = 'gold_keywords.json'; base_dir = $docDir; runs = $runs }
$out | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $outDir 'kw_raw_results.json') -Encoding UTF8
Write-Host ("SAVED " + (Join-Path $outDir 'kw_raw_results.json'))
