param(
    [Parameter(Mandatory = $true)]
    [string]$ResourceGroup,

    [Parameter(Mandatory = $true)]
    [string]$RegistryName,

    [Parameter(Mandatory = $true)]
    [string]$AppName,

    [string]$Location = "koreacentral",
    [string]$ImageTag = "latest",
    [switch]$WithRuntime,
    [string]$KeyVaultName = "",
    [string]$GitHubTokenSecretUri = ""
)

$ErrorActionPreference = "Stop"
$image = "$RegistryName.azurecr.io/reference-check-agent:$ImageTag"
$runtimeBuildArg = if ($WithRuntime) { "true" } else { "false" }

if ($GitHubTokenSecretUri -and -not $WithRuntime) {
    throw "GitHub token activation requires -WithRuntime."
}

if ($WithRuntime) {
    $wheelhouse = Join-Path ([System.IO.Path]::GetTempPath()) "reference-check-wheelhouse"
    New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
    try {
        # SDK 1.0.2 needs manylinux_2_28; pydantic-core still needs manylinux2014.
        python -m pip download `
            --dest $wheelhouse `
            --requirement backend/requirements-ai.txt `
            --platform manylinux_2_28_x86_64 `
            --platform manylinux2014_x86_64 `
            --only-binary=:all: `
            --python-version 3.12
        if ($LASTEXITCODE -ne 0) { throw "Python 3.12 wheel 검증에 실패했습니다." }
    }
    finally {
        if (Test-Path -LiteralPath $wheelhouse) {
            Remove-Item -LiteralPath $wheelhouse -Recurse -Force
        }
    }
}

az group create --name $ResourceGroup --location $Location --output none
if ($LASTEXITCODE -ne 0) { throw "리소스 그룹 생성에 실패했습니다." }

az acr build `
    --registry $RegistryName `
    --image "reference-check-agent:$ImageTag" `
    --file Dockerfile `
    --build-arg "INSTALL_AI_RUNTIME=$runtimeBuildArg" `
    .
if ($LASTEXITCODE -ne 0) { throw "ACR 이미지 빌드에 실패했습니다." }

az deployment group create `
    --resource-group $ResourceGroup `
    --template-file infra/bicep/main.bicep `
    --parameters location=$Location containerImage=$image appName=$AppName `
        keyVaultName=$KeyVaultName githubTokenSecretUri=$GitHubTokenSecretUri `
    --output table
if ($LASTEXITCODE -ne 0) { throw "Container Apps 배포에 실패했습니다." }

az deployment group show `
    --resource-group $ResourceGroup `
    --name main `
    --query properties.outputs.url.value `
    --output tsv
