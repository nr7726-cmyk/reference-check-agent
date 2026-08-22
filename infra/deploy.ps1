param(
    [Parameter(Mandatory = $true)]
    [string]$ResourceGroup,

    [Parameter(Mandatory = $true)]
    [string]$RegistryName,

    [Parameter(Mandatory = $true)]
    [string]$AppName,

    [string]$Location = "koreacentral",
    [string]$ImageTag = "latest"
)

$ErrorActionPreference = "Stop"
$image = "$RegistryName.azurecr.io/reference-check-agent:$ImageTag"

az group create --name $ResourceGroup --location $Location --output none
if ($LASTEXITCODE -ne 0) { throw "리소스 그룹 생성에 실패했습니다." }

az acr build `
    --registry $RegistryName `
    --image "reference-check-agent:$ImageTag" `
    --file Dockerfile `
    .
if ($LASTEXITCODE -ne 0) { throw "ACR 이미지 빌드에 실패했습니다." }

az deployment group create `
    --resource-group $ResourceGroup `
    --template-file infra/bicep/main.bicep `
    --parameters location=$Location containerImage=$image appName=$AppName `
    --output table
if ($LASTEXITCODE -ne 0) { throw "Container Apps 배포에 실패했습니다." }

az deployment group show `
    --resource-group $ResourceGroup `
    --name main `
    --query properties.outputs.url.value `
    --output tsv
