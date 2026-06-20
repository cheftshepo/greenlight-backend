#!/bin/bash
# Azure Marketplace Integration Deployment Script
# Run this after completing Partner Center setup

set -e

echo "🚀 DLA Compliance Platform - Azure Marketplace Deployment"
echo "=========================================================="
echo ""

# Configuration
RESOURCE_GROUP="rg-dla-compliance-poc"
FUNCTION_APP_NAME="dla-compliance-api"
COSMOS_ACCOUNT="dla-compliance-cosmos"
COSMOS_DATABASE="compliance-db"
APP_URL="https://dla-compliance.azurewebsites.net"

# Collect Azure Marketplace credentials
echo "📋 Please provide your Azure Marketplace credentials:"
echo ""
read -p "Azure AD Tenant ID: " TENANT_ID
read -p "Marketplace App Registration Client ID: " CLIENT_ID
read -sp "Marketplace App Registration Secret: " CLIENT_SECRET
echo ""
echo ""

# Validate inputs
if [ -z "$TENANT_ID" ] || [ -z "$CLIENT_ID" ] || [ -z "$CLIENT_SECRET" ]; then
    echo "❌ Error: All credentials are required"
    exit 1
fi

echo "✅ Credentials collected"
echo ""

# Step 1: Create Cosmos DB container
echo "📦 Step 1: Creating marketplace_subscriptions container..."
az cosmosdb sql container create \
  --account-name $COSMOS_ACCOUNT \
  --database-name $COSMOS_DATABASE \
  --name marketplace_subscriptions \
  --partition-key-path "/partition_key" \
  --throughput 400 \
  --yes || echo "⚠️  Container may already exist"

echo "✅ Container created"
echo ""

# Step 2: Set environment variables
echo "🔧 Step 2: Configuring Azure Function App..."

az functionapp config appsettings set \
  --name $FUNCTION_APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --settings \
    AZURE_MARKETPLACE_TENANT_ID=$TENANT_ID \
    AZURE_MARKETPLACE_CLIENT_ID=$CLIENT_ID \
    AZURE_MARKETPLACE_CLIENT_SECRET=$CLIENT_SECRET \
    APP_URL=$APP_URL

echo "✅ Environment variables configured"
echo ""

# Step 3: Verify endpoint accessibility
echo "🔍 Step 3: Verifying marketplace endpoints..."

LANDING_URL="https://${FUNCTION_APP_NAME}.azurewebsites.net/marketplace/activate"
WEBHOOK_URL="https://${FUNCTION_APP_NAME}.azurewebsites.net/marketplace/webhook"

echo "Testing landing page endpoint..."
LANDING_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$LANDING_URL")

if [ "$LANDING_STATUS" = "400" ] || [ "$LANDING_STATUS" = "200" ]; then
    echo "✅ Landing page endpoint: $LANDING_URL (HTTP $LANDING_STATUS)"
else
    echo "⚠️  Landing page endpoint: $LANDING_URL (HTTP $LANDING_STATUS - may need deployment)"
fi

echo "Testing webhook endpoint..."
WEBHOOK_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$WEBHOOK_URL" -H "Content-Type: application/json" -d '{}')

if [ "$WEBHOOK_STATUS" = "200" ] || [ "$WEBHOOK_STATUS" = "400" ]; then
    echo "✅ Webhook endpoint: $WEBHOOK_URL (HTTP $WEBHOOK_STATUS)"
else
    echo "⚠️  Webhook endpoint: $WEBHOOK_URL (HTTP $WEBHOOK_STATUS - may need deployment)"
fi

echo ""

# Step 4: Provide Partner Center configuration
echo "🎯 Step 4: Partner Center Configuration"
echo "========================================"
echo ""
echo "Copy these URLs into Partner Center → Technical Configuration:"
echo ""
echo "Landing Page URL:"
echo "  $LANDING_URL"
echo ""
echo "Connection Webhook URL:"
echo "  $WEBHOOK_URL"
echo ""
echo "Azure AD Tenant ID:"
echo "  $TENANT_ID"
echo ""
echo "Azure AD Application ID:"
echo "  $CLIENT_ID"
echo ""

# Step 5: Deployment checklist
echo "✅ Deployment Complete!"
echo ""
echo "📋 Next Steps:"
echo "1. Update Partner Center technical configuration with URLs above"
echo "2. Submit your test offer for review"
echo "3. Test purchase flow once approved"
echo "4. Monitor Azure Functions logs during testing"
echo "5. Once tested, submit production offer"
echo ""
echo "📖 Full documentation: see MARKETPLACE_SETUP_GUIDE.md"
echo ""
echo "🆘 Support: support@yourcompany.com"
echo ""