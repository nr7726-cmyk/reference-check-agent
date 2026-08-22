@description('Optional Container Apps/ACR deployment for subscriptions with ACR Tasks permission.')
param location string = resourceGroup().location

@description('Full container image reference in an existing Azure Container Registry.')
param containerImage string

@description('Globally unique Container App name.')
param appName string

@description('Optional existing Key Vault name that owns the GitHub token secret.')
param keyVaultName string = ''

@secure()
@description('Optional full Key Vault secret URI. Omit to keep the AI kill switch off.')
param githubTokenSecretUri string = ''

var workspaceName = '${appName}-logs'
var environmentName = '${appName}-env'
var insightsName = '${appName}-insights'
var identityName = '${appName}-identity'
var acrName = split(containerImage, '.')[0]
var aiConfigured = !empty(keyVaultName) && !empty(githubTokenSecretUri)
var acrPullRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '7f951dda-4ed3-4680-a7ca-43fe172d538d'
)

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

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: workspace.properties.customerId
        sharedKey: workspace.listKeys().primarySharedKey
      }
    }
  }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = if (aiConfigured) {
  name: keyVaultName
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: '${acr.name}.azurecr.io'
          identity: identity.id
        }
      ]
      secrets: aiConfigured ? [
        {
          name: 'github-token'
          keyVaultUrl: githubTokenSecretUri
          identity: identity.id
        }
      ] : []
    }
    template: {
      containers: [
        {
          name: 'web'
          image: containerImage
          env: concat([
            {
              name: 'PORT'
              value: '8000'
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: insights.properties.ConnectionString
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
              value: '/opt/copilot/copilot'
            }
            {
              name: 'COPILOT_HOME'
              value: '/home/app/copilot'
            }
          ], aiConfigured ? [
            {
              name: 'GITHUB_TOKEN'
              secretRef: 'github-token'
            }
          ] : [])
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health/live'
                port: 8000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 10
              periodSeconds: 20
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health/ready'
                port: 8000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
  dependsOn: aiConfigured ? [
    keyVaultAccess
  ] : []
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, identity.id, acrPullRoleId)
  scope: acr
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleId
  }
}

var keyVaultSecretsUserRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '4633458b-17de-408a-b874-0445c86b69e6'
)

resource keyVaultAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (aiConfigured) {
  name: guid(keyVault.id, identity.id, keyVaultSecretsUserRoleId)
  scope: keyVault
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRoleId
  }
}

output url string = 'https://${app.properties.configuration.ingress.fqdn}'
output containerAppName string = app.name
