param(
    [Parameter(Mandatory = $true)]
    [string]$ResourceGroup,

    [Parameter(Mandatory = $true)]
    [string]$AppName,

    [string]$Location = "koreacentral",
    [string]$AppServicePlanName = "",
    [switch]$WithRuntime,
    [string]$KeyVaultName = "",
    [string]$GitHubTokenSecretUri = "",

    [switch]$UseContainerAlternative,
    [string]$RegistryName = "",
    [string]$ImageTag = "latest"
)

$ErrorActionPreference = "Stop"
$resolvedPlanName = if ($AppServicePlanName) { $AppServicePlanName } else { "$AppName-plan" }

if ($GitHubTokenSecretUri -and -not $WithRuntime) {
    throw "GitHub token activation requires -WithRuntime."
}
if ($UseContainerAlternative -and -not $RegistryName) {
    throw "-UseContainerAlternative requires -RegistryName."
}

az group create --name $ResourceGroup --location $Location --output none
if ($LASTEXITCODE -ne 0) { throw "리소스 그룹 생성에 실패했습니다." }

if ($UseContainerAlternative) {
    $runtimeBuildArg = if ($WithRuntime) { "true" } else { "false" }
    $image = "$RegistryName.azurecr.io/reference-check-agent:$ImageTag"
    az acr build `
        --registry $RegistryName `
        --image "reference-check-agent:$ImageTag" `
        --file Dockerfile `
        --build-arg "INSTALL_AI_RUNTIME=$runtimeBuildArg" `
        .
    if ($LASTEXITCODE -ne 0) {
        throw "ACR Tasks 권한이 필요합니다. 기본 App Service 배포를 사용하십시오."
    }

    az deployment group create `
        --resource-group $ResourceGroup `
        --template-file infra/bicep/container-apps.bicep `
        --parameters location=$Location containerImage=$image appName=$AppName `
            keyVaultName=$KeyVaultName githubTokenSecretUri=$GitHubTokenSecretUri `
        --output table
    if ($LASTEXITCODE -ne 0) { throw "선택적 Container Apps 배포에 실패했습니다." }
    return
}

$aiEnabled = [bool]($WithRuntime -and $GitHubTokenSecretUri)
az deployment group create `
    --resource-group $ResourceGroup `
    --template-file infra/bicep/main.bicep `
    --parameters location=$Location appName=$AppName appServicePlanName=$resolvedPlanName `
        keyVaultName=$KeyVaultName githubTokenSecretUri=$GitHubTokenSecretUri `
        aiEnabled=$($aiEnabled.ToString().ToLowerInvariant()) `
    --output table
if ($LASTEXITCODE -ne 0) { throw "App Service 인프라 배포에 실패했습니다." }

$packageRoot = Join-Path ([System.IO.Path]::GetTempPath()) "reference-check-package-$([guid]::NewGuid())"
$zipPath = "$packageRoot.zip"
$requirements = if ($WithRuntime) {
    "backend/requirements-ai.txt"
}
else {
    "backend/requirements.txt"
}

try {
    New-Item -ItemType Directory -Path $packageRoot | Out-Null

    & npm.cmd ci --prefix frontend
    if ($LASTEXITCODE -ne 0) { throw "프론트엔드 의존성 설치에 실패했습니다." }
    & npm.cmd run build --prefix frontend
    if ($LASTEXITCODE -ne 0) { throw "프론트엔드 빌드에 실패했습니다." }

    Copy-Item -LiteralPath backend/app -Destination (Join-Path $packageRoot "app") -Recurse
    Copy-Item -LiteralPath frontend/dist -Destination (Join-Path $packageRoot "static") -Recurse

    $sitePackages = Join-Path $packageRoot ".python_packages/lib/site-packages"
    python -m pip install `
        --requirement $requirements `
        --target $sitePackages `
        --platform manylinux_2_28_x86_64 `
        --platform manylinux2014_x86_64 `
        --python-version 3.12 `
        --implementation cp `
        --abi cp312 `
        --only-binary=:all:
    if ($LASTEXITCODE -ne 0) { throw "Python 3.12 Linux wheel vendoring에 실패했습니다." }

    $env:PACKAGE_ROOT = $packageRoot
    $env:PACKAGE_ZIP = $zipPath
    @'
import os
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(os.environ["PACKAGE_ROOT"])
archive = Path(os.environ["PACKAGE_ZIP"])
with ZipFile(archive, "w", ZIP_DEFLATED) as target:
    for source in root.rglob("*"):
        if source.is_file():
            target.write(source, source.relative_to(root).as_posix())
'@ | python -
    if ($LASTEXITCODE -ne 0) { throw "POSIX 경로 ZIP 생성에 실패했습니다." }

    az webapp deploy `
        --resource-group $ResourceGroup `
        --name $AppName `
        --type zip `
        --src-path $zipPath `
        --clean true `
        --restart false `
        --output none
    if ($LASTEXITCODE -ne 0) { throw "App Service ZIP 배포에 실패했습니다." }

    az webapp restart --resource-group $ResourceGroup --name $AppName
    if ($LASTEXITCODE -ne 0) { throw "App Service 재시작에 실패했습니다." }

    "https://$AppName.azurewebsites.net"
}
finally {
    Remove-Item Env:PACKAGE_ROOT -ErrorAction SilentlyContinue
    Remove-Item Env:PACKAGE_ZIP -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $packageRoot) {
        Remove-Item -LiteralPath $packageRoot -Recurse -Force
    }
    if (Test-Path -LiteralPath $zipPath) {
        Remove-Item -LiteralPath $zipPath -Force
    }
}
