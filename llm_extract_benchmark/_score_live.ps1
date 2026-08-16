$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Web.Extensions
$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$docDir = 'C:\Users\guhao\PyCharmMiscProject\igf_classified_20260812_060303\_invalid\other'
$ser = New-Object System.Web.Script.Serialization.JavaScriptSerializer
$ser.MaxJsonLength = 2147483647
$gold = $ser.DeserializeObject([IO.File]::ReadAllText((Join-Path $base 'gold_keywords.json')))
$res = $ser.DeserializeObject([IO.File]::ReadAllText((Join-Path $base 'results_kw\kw_raw_results.json')))
$stop = 'the a an and or but of to in on for with as at by is are was were be been this that these those it its from we you they he she i not no so if then than into over under out up down who whom which what when where why how can could may might must shall should will would do does did have has had about above after again against all also am an any because before below between both during each few further here him his her hers more most much myself nor once only other own same some such than too very just s t d m re ve ll don isn aren wasn weren doesnt didnt'
$stopSet = @{}; $stop.Split(' ') | ForEach-Object { $stopSet[$_] = $true }

function Normalize([string]$s) {
    $t = $s.ToLower() -replace "[^a-z0-9' -]", ' '
    $t = ($t -replace '\s+', ' ').Trim()
    $t.Trim(" -'")
}
function F1($a, $b) {
    if ($a.Count -eq 0 -and $b.Count -eq 0) { return 1.0 }
    if ($a.Count -eq 0 -or $b.Count -eq 0) { return 0.0 }
    $inter = @($a | Where-Object { $b -contains $_ }).Count
    $p = $inter / $a.Count; $r = $inter / $b.Count
    if ($p + $r -eq 0) { 0.0 } else { 2 * $p * $r / ($p + $r) }
}
function SoftMatch([string]$g, $predSet) {
    $gt = @($g -split '\s+' | Where-Object { $_ })
    foreach ($p in $predSet) {
        if ($g -eq $p) { return 1.0 }
        $pt = @($p -split '\s+' | Where-Object { $_ })
        if ($gt.Count -ge 2 -and $pt.Count -ge 2) {
            if ($gt[0] -eq $pt[0] -and $gt[-1] -eq $pt[-1]) { return 0.7 }
            foreach ($x in $gt) { if ($pt -contains $x) { return 0.5 } }
        }
        if ($gt.Count -eq 1 -and $pt.Count -ge 1 -and ($pt -contains $gt[0])) { return 0.5 }
    }
    0.0
}
function Score-Kws($goldKws, $predKws) {
    $g = @($goldKws | ForEach-Object { Normalize ([string]$_.kw) } | Where-Object { $_ })
    $p = @($predKws | ForEach-Object { Normalize ([string]$_) } | Where-Object { $_ })
    if ($p.Count -eq 0) {
        return [pscustomobject]@{phrase_f1=0.0; token_f1=0.0; soft_recall=0.0; soft_precision=0.0; exact_hit_rate=0.0; n_pred=0; n_gold=$g.Count}
    }
    $gu = @($g | Sort-Object -Unique); $pu = @($p | Sort-Object -Unique)
    $phrase = F1 $gu $pu
    $tg = @(); foreach ($x in $gu) { $tg += @($x -split '\s+' | Where-Object { $_ }) }
    $tp = @(); foreach ($x in $pu) { $tp += @($x -split '\s+' | Where-Object { $_ }) }
    $token = F1 (@($tg | Sort-Object -Unique)) (@($tp | Sort-Object -Unique))
    $sr = ($gu | ForEach-Object { SoftMatch $_ $pu } | Measure-Object -Sum).Sum / $gu.Count
    $sp = ($pu | ForEach-Object { SoftMatch $_ $gu } | Measure-Object -Sum).Sum / $pu.Count
    $exact = (@($gu | Where-Object { $pu -contains $_ }).Count) / $gu.Count
    [pscustomobject]@{phrase_f1=[math]::Round($phrase,4); token_f1=[math]::Round($token,4);
                      soft_recall=[math]::Round($sr,4); soft_precision=[math]::Round($sp,4);
                      exact_hit_rate=[math]::Round($exact,4); n_pred=$pu.Count; n_gold=$gu.Count}
}
function Get-Window([string]$file) {
    $raw = [IO.File]::ReadAllText((Join-Path $docDir $file), [Text.Encoding]::UTF8)
    $t = $raw -replace '(?is)<script.*?</script>', ' ' -replace '(?is)<style.*?</style>', ' ' -replace '<[^>]+>', ' '
    $t = $t -replace '&amp;', '&' -replace '&gt;', '>' -replace '&lt;', '<' -replace '&quot;', '"' -replace '&#\d+;', ' '
    $t = ($t -replace '\s+', ' ').Trim()
    if ($t.Length -gt 4000) { $t.Substring(0, 4000) } else { $t }
}
$rows = @()
foreach ($run in $res.runs) {
    $g = $gold | Where-Object { $_.doc -eq $run.doc } | Select-Object -First 1
    $s = Score-Kws $g.keywords $run.keywords
    $rows += [pscustomobject]@{model=[string]$run.model; method=[string]$run.method; doc=[string]$run.doc;
        phrase_f1=$s.phrase_f1; token_f1=$s.token_f1; soft_recall=$s.soft_recall; soft_precision=$s.soft_precision;
        exact_hit_rate=$s.exact_hit_rate; n_pred=$s.n_pred; n_gold=$s.n_gold; source=[string]$run.source; parse=$(if($run.parsed){'ok'}else{'fail'})}
}
foreach ($g in $gold) {
    $win = Get-Window ([string]$g.file)
    $words = @([regex]::Matches($win.ToLower(), '[a-z]+') | ForEach-Object { $_.Value } | Where-Object { $_.Length -gt 3 -and -not $stopSet.ContainsKey($_) })
    $freq = @{}; foreach ($w in $words) { $freq[$w] = 1 + $freq[$w] }
    $top = @($freq.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 12 | ForEach-Object { $_.Key })
    $s = Score-Kws $g.keywords $top
    $rows += [pscustomobject]@{model='tf-baseline'; method='tf'; doc=[string]$g.doc;
        phrase_f1=$s.phrase_f1; token_f1=$s.token_f1; soft_recall=$s.soft_recall; soft_precision=$s.soft_precision;
        exact_hit_rate=$s.exact_hit_rate; n_pred=$s.n_pred; n_gold=$s.n_gold; source='na'; parse='ok'}
}
$rows | Export-Csv -LiteralPath (Join-Path $base 'results_kw\kw_metrics.csv') -NoTypeInformation -Encoding UTF8
$agg = @{}
foreach ($r in $rows) {
    $k = "$($r.model)|$($r.method)"
    if (-not $agg.ContainsKey($k)) { $agg[$k] = @() }
    $agg[$k] += $r
}
$rep = New-Object System.Text.StringBuilder
[void]$rep.AppendLine("Keyword extraction vs gold labels (similarity-based evaluation)")
[void]$rep.AppendLine("Gold docs: $($gold.Count)")
foreach ($k in @($agg.Keys | Sort-Object)) {
    $rs = $agg[$k]
    $n = $rs.Count
    $pf = ($rs | Measure-Object phrase_f1 -Average).Average
    $tf = ($rs | Measure-Object token_f1 -Average).Average
    $sr = ($rs | Measure-Object soft_recall -Average).Average
    $sp = ($rs | Measure-Object soft_precision -Average).Average
    $ex = ($rs | Measure-Object exact_hit_rate -Average).Average
    $fail = @($rs | Where-Object { $_.parse -ne 'ok' }).Count
    [void]$rep.AppendLine(("{0,-18} {1,-8} n={2,-2} phraseF1={3:N3} tokenF1={4:N3} softR={5:N3} softP={6:N3} exact={7:N3} parse_fail={8}" -f $k.Split('|')[0],$k.Split('|')[1],$n,$pf,$tf,$sr,$sp,$ex,$fail))
}
[IO.File]::WriteAllText((Join-Path $base 'results_kw\kw_report.txt'), $rep.ToString(), [Text.Encoding]::UTF8)
Write-Host 'wrote results_kw\kw_metrics.csv and results_kw\kw_report.txt'
$rep.ToString()
