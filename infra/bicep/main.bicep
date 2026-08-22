@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Globally unique Linux App Service name.')
param appName string

@description('Existing or new App Service Plan name.')
param appServicePlanName string = '${appName}-plan'

@description('Optional existing Key Vault name that owns the GitHub token secret.')
param keyVaultName string = ''

@secure()
@description('Optional full Key Vault secret URI. Omit to keep the AI kill switch off.')
param githubTokenSecretUri string = ''

@description('Enable AI only when the runtime and Key Vault token are both configured.')
param aiEnabled bool = false

var workspaceName = '${appName}-logs'
var insightsName = '${appName}-insights'
var aiConfigured = aiEnabled && !empty(keyVaultName) && !empty(githubTokenSecretUri)

resource workspace 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: workspaceName
  location: location
  properties: {
    retentionInDays: 30
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: insightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
  }
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: appServicePlanName
  location: location
  sku: {
    name: 'B1'
    tier: 'Basic'
    capacity: 1
  }
  kind: 'linux'
  properties: {
    reserved: true
  }
}

resource app 'Microsoft.Web/sites@2023-12-01' = {
  name: appName
  location: location
  kind: 'app,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    clientAffinityEnabled: false
    publicNetworkAccess: 'Enabled'
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      appCommandLine: 'python -m uvicorn app.main:app --host 0.0.0.0 --port 8000'
      alwaysOn: true
      ftpsState: 'Disabled'
      http20Enabled: true
      minTlsVersion: '1.2'
      healthCheckPath: '/health/live'
      appSettings: concat([
        {
          name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
          value: 'false'
        }
        {
          name: 'ENABLE_ORYX_BUILD'
          value: 'false'
        }
        {
          name: 'STATIC_DIR'
          value: '/home/site/wwwroot/static'
        }
        {
          name: 'AI_ENABLED'
          value: aiConfigured ? 'true' : 'false'
        }
        {
          name: 'COPILOT_SKIP_CLI_DOWNLOAD'
          value: '1'
        }
        {
          name: 'COPILOT_CLI_PATH'
          value: '/home/site/wwwroot/.python_packages/lib/site-packages/copilot/bin/copilot'
        }
        {
          name: 'COPILOT_HOME'
          value: '/home/copilot'
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: insights.properties.ConnectionString
        }
      ], aiConfigured ? [
        {
          name: 'GITHUB_TOKEN'
          value: '@Microsoft.KeyVault(SecretUri=${githubTokenSecretUri})'
        }
      ] : [])
    }
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = if (aiConfigured) {
  name: keyVaultName
}

var keyVaultSecretsUserRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '4633458b-17de-408a-b874-0445c86b69e6'
)

resource keyVaultAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (aiConfigured) {
  name: guid(keyVault.id, app.id, keyVaultSecretsUserRoleId)
  scope: keyVault
  properties: {
    principalId: app.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRoleId
  }
}

output url string = 'https://${app.properties.defaultHostName}'
output appServiceName string = app.name
