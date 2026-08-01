param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Instruction,

    [ValidateSet("plan", "propose", "apply")]
    [string]$Stage = "plan"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ParentRoot = Split-Path -Parent $ProjectRoot

Push-Location $ParentRoot
try {
    & python -m artmach_assistant --self-develop $Instruction --self-develop-stage $Stage
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
