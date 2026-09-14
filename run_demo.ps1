<#
.SYNOPSIS
    Demonstracao em um comando (Windows / PowerShell).

.DESCRIPTION
    Instala as dependencias, prepara a base e gera o relatorio pela via
    deterministica -- sem exigir chave de API. Equivale a `make demo`.

.EXAMPLE
    .\run_demo.ps1                                   # baixa 2022-2026 do DATASUS
    .\run_demo.ps1 -Csv .\INFLUD25-DATASUS-Versao26-06-2025.csv   # base do enunciado
    .\run_demo.ps1 -Uf SP -Llm                        # recorte por UF, com LLM (.env)
#>
param(
    [string[]]$Years = @(2022, 2023, 2024, 2025, 2026),
    [string]$Csv = "",
    [string]$Uf = "",
    [switch]$Llm,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not $SkipInstall) {
    Write-Host "[1/3] Instalando dependencias..." -ForegroundColor Cyan
    python -m pip install -q -r requirements.txt
}

$arguments = @("main.py", "--setup")
if ($Csv) { $arguments += @("--csv", $Csv) } else { $arguments += @("--years") + $Years }
if ($Uf) { $arguments += @("--uf", $Uf) }
if (-not $Llm) { $arguments += "--no-llm" }

Write-Host "[2/3] Preparando a base e gerando o relatorio..." -ForegroundColor Cyan
Write-Host "      python $($arguments -join ' ')"
python @arguments
$status = $LASTEXITCODE

Write-Host "[3/3] Concluido (codigo $status)." -ForegroundColor Cyan
$latest = Get-ChildItem outputs\reports\*.md -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($latest) { Write-Host "      Relatorio: $($latest.FullName)" }
exit $status
