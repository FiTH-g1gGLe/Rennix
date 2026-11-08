# Downloads the newest MKVToolNix for Windows and keeps just mkvpropedit.exe in .\tools
# (used by the GitHub build; Rennix ships this one small tool for writing MKV tags).
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$index = (Invoke-WebRequest -UseBasicParsing "https://mkvtoolnix.download/windows/releases/").Content
$versions = [regex]::Matches($index, 'href="(\d+\.\d+(?:\.\d+)?)/"') | ForEach-Object { $_.Groups[1].Value } |
    Sort-Object { [version]$_ } -Unique
if (-not $versions) { throw "Couldn't find any MKVToolNix versions" }
$v = @($versions)[-1]
Write-Host "MKVToolNix $v"
Invoke-WebRequest -UseBasicParsing "https://mkvtoolnix.download/windows/releases/$v/mkvtoolnix-64-bit-$v.7z" -OutFile mkvtoolnix.7z
7z x mkvtoolnix.7z -omkvtmp -y | Out-Null
New-Item -ItemType Directory -Force tools | Out-Null
$exe = Get-ChildItem -Recurse mkvtmp -Filter mkvpropedit.exe | Select-Object -First 1
if (-not $exe) { throw "mkvpropedit.exe not found in the download" }
Copy-Item $exe.FullName tools\mkvpropedit.exe
& tools\mkvpropedit.exe --version
if ($LASTEXITCODE -ne 0) { throw "mkvpropedit.exe doesn't run on its own" }
Set-Content tools\MKVToolNix-NOTICE.txt @"
mkvpropedit.exe is part of MKVToolNix $v by Moritz Bunkus and contributors.
It is free software under the GNU General Public License, version 2.
Website and source code: https://mkvtoolnix.download/
"@
Remove-Item -Recurse -Force mkvtmp, mkvtoolnix.7z
