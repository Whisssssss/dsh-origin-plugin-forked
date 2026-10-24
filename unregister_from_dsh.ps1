# unregister_from_dsh.ps1
# 从 DSH 的 profile 移除 Origin 画图 MCP 插件（自动备份）。
# v2.9.1：不再假设 profile 叫 web；自动探测含 dsh-origin-plugin 段的 profile，
#         多个命中时用 -ProfileName 指定。

param(
    [string]$ProfileName = ""
)

$ErrorActionPreference = "Stop"

$dshHome      = Join-Path $env:USERPROFILE ".dsh"
$profileRoots = @(
    (Join-Path $dshHome "profiles"),
    (Join-Path $env:APPDATA "dsh-desktop\harness\profiles")
)

function Find-RegisteredProfiles {
    $found = @()
    foreach ($root in $profileRoots) {
        if (-not (Test-Path $root)) { continue }
        Get-ChildItem $root -Directory -ErrorAction SilentlyContinue | ForEach-Object {
            $patchFile = Join-Path $_.FullName "cordis.patch.yml"
            if (-not (Test-Path $patchFile)) { return }
            $text = [System.IO.File]::ReadAllText($patchFile, [System.Text.Encoding]::UTF8)
            if ($text -match "dsh-origin-plugin") {
                $found += [pscustomobject]@{ Name = $_.Name; Patch = $patchFile }
            }
        }
    }
    return $found
}

$targets = @(Find-RegisteredProfiles)
if ($ProfileName) {
    $targets = @($targets | Where-Object { $_.Name -eq $ProfileName })
}
if ($targets.Count -eq 0) {
    Write-Host "未在任何 profile 的 cordis.patch.yml 中发现 dsh-origin-plugin 段，无需卸载。"
    Write-Host "（已搜索：$($profileRoots -join '；')）"
    exit 0
}
if ($targets.Count -gt 1) {
    $names = ($targets | ForEach-Object { $_.Name }) -join "、"
    throw "多个 profile 都含 dsh-origin-plugin 段（$names）。请用 -ProfileName <名字> 指定要卸载哪一个。"
}

$profilePatch = $targets[0].Patch
Write-Host "目标 profile: $($targets[0].Name)  ($profilePatch)"

$content = [System.IO.File]::ReadAllText($profilePatch, [System.Text.Encoding]::UTF8)
$bak = "$profilePatch.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
Copy-Item $profilePatch $bak
Write-Host "已备份 -> $bak"

$pattern = "(?s)\r?\n# --- dsh-origin-plugin.*?# --- end dsh-origin-plugin ---\r?\n?"
$new = [System.Text.RegularExpressions.Regex]::Replace($content, $pattern, "`r`n")
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($profilePatch, $new, $utf8NoBom)
Write-Host "已移除 dsh-origin-plugin 段。重启 Harness 生效。"
