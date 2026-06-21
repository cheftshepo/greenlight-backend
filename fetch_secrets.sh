#!/bin/bash

# === CONFIGURATION ===
VAULT_NAME="kv-nsync-dla-poc"   # your Key Vault name

# Map Key Vault secret names → environment variable names used in local.settings.json
declare -A SECRET_MAP=(
  ["AZURE-CLIENT-SECRET"]="AZURE_CLIENT_SECRET"
  ["AZURE-OPENAI-API-KEY"]="AZURE_OPENAI_API_KEY"
  ["AZURE-SEARCH-KEY"]="AZURE_SEARCH_KEY"
  ["AZURE-STORAGE-ACCOUNT-KEY"]="AZURE_STORAGE_ACCOUNT_KEY"
  ["AZURE-STORAGE-CONNECTION-STRING"]="AZURE_STORAGE_CONNECTION_STRING"
  ["COMPUTER-VISION-KEY"]="COMPUTER_VISION_KEY"
  ["COSMOS-KEY"]="COSMOS_KEY"
  ["DOCUMENT-INTELLIGENCE-KEY"]="DOCUMENT_INTELLIGENCE_KEY"
  ["JWT-SECRET"]="JWT_SECRET"
  ["LANGUAGE-SERVICE-KEY"]="LANGUAGE_SERVICE_KEY"
  ["TRANSLATOR-KEY"]="TRANSLATOR_KEY"
)

# === CHECK AZURE CLI ===
if ! command -v az &> /dev/null; then
    echo "❌ Azure CLI not found. Install it: https://aka.ms/installazureclibash"
    exit 1
fi

# Check login status
az account show &> /dev/null
if [ $? -ne 0 ]; then
    echo "🔐 Please log in to Azure first:"
    az login
fi

# === FETCH SECRETS ===
echo "🔑 Fetching secrets from '$VAULT_NAME'..."
for kv_name in "${!SECRET_MAP[@]}"; do
    value=$(az keyvault secret show --vault-name "$VAULT_NAME" --name "$kv_name" --query value -o tsv 2>/dev/null)
    if [ $? -eq 0 ] && [ -n "$value" ]; then
        export "${SECRET_MAP[$kv_name]}"="$value"
        echo "  ✓ ${SECRET_MAP[$kv_name]}"
    else
        echo "  ✗ Failed to fetch '$kv_name' – check the secret name and your permissions."
    fi
done

# === GENERATE local.settings.json ===
cat > local.settings.json <<EOF
{
  "IsEncrypted": false,
  "Values": {
    "AzureWebJobsStorage": "DefaultEndpointsProtocol=https;AccountName=rgdlacompliancepoc94d0;AccountKey=Ju2s0q+XXxyjX/IR3757wkEOfo9MyCi20a4bL+ULq0deVIZvhGInFNLfnL8O31/lW1FIe6JsaTgl+AStdx4Qpg==;EndpointSuffix=core.windows.net",
    "FUNCTIONS_EXTENSION_VERSION": "~4",
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=2b2c19dc-2213-4d84-9f04-5027286f4d04;IngestionEndpoint=https://uksouth-1.in.applicationinsights.azure.com/;LiveEndpoint=https://uksouth.livediagnostics.monitor.azure.com/;ApplicationId=d7416927-9430-4e9b-a888-a56e80c1ae44",
    "AZURE_AUTHORITY": "https://login.microsoftonline.com/common",
    "AZURE_CLIENT_ID": "243aa502-d666-4b43-856a-e2921fd8d1be",
    "AZURE_CLIENT_SECRET": "$AZURE_CLIENT_SECRET",
    "AZURE_TENANT_ID": "48f84f65-1c03-4d85-aa81-7a83061e62e6",
    "AZURE_OPENAI_API_KEY": "$AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_API_VERSION": "2025-01-01-preview",
    "AZURE_OPENAI_ENDPOINT": "https://oai-nsyncazr-dla-compliance-poc.openai.azure.com/",
    "AZURE_OPENAI_DEPLOYMENT_NAME": "gpt-4.1",
    "AZURE_OPENAI_DEPLOYMENT": "gpt-4.1",
    "AZURE_OPENAI_MODEL": "gpt-4.1",
    "AZURE_SEARCH_ENDPOINT": "https://langnsyncdlacompliancepoc-asy3fv3yj5hcewo.search.windows.net",
    "AZURE_SEARCH_KEY": "$AZURE_SEARCH_KEY",
    "AZURE_STORAGE_ACCOUNT_KEY": "$AZURE_STORAGE_ACCOUNT_KEY",
    "AZURE_STORAGE_CONNECTION_STRING": "$AZURE_STORAGE_CONNECTION_STRING",
    "AZURE_STORAGE_CONTAINER": "documents",
    "COSMOS_DATABASE": "compliance-platform",
    "COSMOS_ENDPOINT": "https://cosmos-dla-compliance-mvp.documents.azure.com:443/",
    "COSMOS_KEY": "$COSMOS_KEY",
    "COMPUTER_VISION_KEY": "$COMPUTER_VISION_KEY",
    "DOCUMENT_INTELLIGENCE_KEY": "$DOCUMENT_INTELLIGENCE_KEY",
    "LANGUAGE_SERVICE_KEY": "$LANGUAGE_SERVICE_KEY",
    "TRANSLATOR_KEY": "$TRANSLATOR_KEY",
    "JWT_SECRET": "$JWT_SECRET"
  },
  "Host": {
    "LocalHttpPort": 7071,
    "CORS": "*",
    "CORSCredentials": false
  }
}
EOF

echo "✅ local.settings.json has been created in the current directory."