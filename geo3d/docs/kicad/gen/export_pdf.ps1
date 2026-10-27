# Regenerates the KiCad schematic from the RTL and exports geo3d_logic.pdf.
# Needs KiCad 10 (per-user install: winget install KiCad.KiCad).
param([string]$KiCad = "$env:LOCALAPPDATA\Programs\KiCad\10.0\bin")
$ErrorActionPreference = 'Stop'
$gen = $PSScriptRoot
$out = Split-Path $gen -Parent
& "$KiCad\python.exe" "$gen\gen.py"
if ($LASTEXITCODE -ne 0) { throw "gen.py failed" }
& "$KiCad\kicad-cli.exe" sch export pdf -o "$out\geo3d_logic.pdf" "$out\geo3d_logic.kicad_sch"
if ($LASTEXITCODE -ne 0) { throw "PDF export failed" }
Write-Host "PDF: $out\geo3d_logic.pdf"
