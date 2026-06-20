# AZURE MARKETPLACE INTEGRATION - SETUP & DEPLOYMENT

## STEP 1: Add Routes to function_app.py

```python
# Add these imports at the top
from function_app_pkg.api import marketplace

# Add these routes in your route definitions section
@app.route(route="marketplace/activate", methods=["GET"])
def marketplace_landing_page(req: func.HttpRequest) -> func.HttpResponse:
    """Landing page after customer subscribes in Azure Marketplace"""
    return marketplace.handle_marketplace_landing(req)


@app.route(route="marketplace/webhook", methods=["POST"])
def marketplace_webhook(req: func.HttpRequest) -> func.HttpResponse:
    """Webhook for subscription lifecycle events from Microsoft"""
    return marketplace.handle_marketplace_webhook(req)


@app.route(route="marketplace/subscription/{subscriptionId}", methods=["GET"])
def get_marketplace_subscription(req: func.HttpRequest) -> func.HttpResponse:
    """Get subscription details (admin only)"""
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    # Verify user is admin
    user_roles = getattr(user, 'roles', [])
    if 'Platform.SuperAdmin' not in user_roles:
        return json_response(403, error="Admin access required")
    
    return marketplace.handle_get_subscription(req)
```

---

## STEP 2: Environment Variables

Add these to your Azure Function App Configuration:

```bash
# Azure Marketplace Integration
AZURE_MARKETPLACE_TENANT_ID=<your Azure AD tenant ID>
AZURE_MARKETPLACE_CLIENT_ID=<app registration client ID>
AZURE_MARKETPLACE_CLIENT_SECRET=<app registration secret>

# Your app URL (for redirects after activation)
APP_URL=https://dla-compliance.azurewebsites.net
```

**How to get these:**

1. **Tenant ID:** Azure Portal → Azure Active Directory → Overview → copy Tenant ID

2. **Client ID & Secret:** (from Part 1, Step 3.4)
   - Azure Portal → Azure Active Directory → App registrations
   - Find "DLA Compliance Marketplace Integration"
   - Copy Application (client) ID
   - Certificates & secrets → copy secret value

---

## STEP 3: Create Cosmos DB Container

```bash
# Create marketplace_subscriptions container
az cosmosdb sql container create \
  --account-name <your-cosmos-account> \
  --database-name <your-database-name> \
  --name marketplace_subscriptions \
  --partition-key-path "/partition_key" \
  --throughput 400
```

**Or via Azure Portal:**
1. Cosmos DB → Data Explorer → New Container
2. Container ID: `marketplace_subscriptions`
3. Partition key: `/partition_key`
4. Throughput: 400 RU/s (scales with usage)

---

## STEP 4: Frontend - Success Landing Page

Create `app/(marketing)/marketplace/success/page.tsx`:

```tsx
'use client';

import { useEffect, useState } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { CheckCircle, Loader2 } from 'lucide-react';

export default function MarketplaceSuccessPage() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const [status, setStatus] = useState<'loading' | 'success' | 'error'>('loading');
  
  const subscriptionId = searchParams.get('subscription_id');
  const planId = searchParams.get('plan');
  
  useEffect(() => {
    // Optional: Call your backend to verify activation
    // For now, just show success after 2 seconds
    const timer = setTimeout(() => {
      setStatus('success');
    }, 2000);
    
    return () => clearTimeout(timer);
  }, []);
  
  if (status === 'loading') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-950">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Loader2 className="h-5 w-5 animate-spin" />
              Activating Subscription
            </CardTitle>
            <CardDescription>
              Setting up your DLA Compliance Platform account...
            </CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-slate-400">
              This will only take a moment.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }
  
  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-950 p-4">
      <Card className="w-full max-w-md border-green-500/20 bg-green-500/5">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-green-400">
            <CheckCircle className="h-6 w-6" />
            Subscription Activated!
          </CardTitle>
          <CardDescription>
            Your DLA Compliance Platform account is ready to use.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="p-4 bg-slate-900/50 rounded-lg space-y-2">
            <div className="flex justify-between text-sm">
              <span className="text-slate-400">Subscription ID:</span>
              <span className="text-slate-200 font-mono text-xs">
                {subscriptionId?.slice(0, 8)}...
              </span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-slate-400">Plan:</span>
              <span className="text-slate-200 capitalize">
                {planId}
              </span>
            </div>
          </div>
          
          <div className="space-y-2">
            <p className="text-sm text-slate-300">
              <strong>What's next:</strong>
            </p>
            <ul className="text-sm text-slate-400 space-y-1 ml-4 list-disc">
              <li>Sign in with your Microsoft account</li>
              <li>Upload your first document</li>
              <li>Invite your team members</li>
            </ul>
          </div>
          
          <Button 
            onClick={() => router.push('/login')}
            className="w-full"
          >
            Go to Dashboard
          </Button>
          
          <p className="text-xs text-slate-500 text-center">
            Questions? Email support@yourcompany.com
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
```

---

## STEP 5: Testing Guide

### 5.1 Create Test Offer

**In Partner Center:**
1. Create a second offer with ID: `dla-compliance-platform-test`
2. Same as production offer but:
   - Add your Azure subscription ID to "Preview audience"
   - Set pricing to £1/month for testing
   - Submit for review (faster approval since it's test)

### 5.2 Test Purchase Flow

**Once test offer approved:**

1. **Go to Azure Marketplace in Portal**
   - Search for your test offer (only you can see it)
   - Click "Subscribe"

2. **Fill out subscription form**
   ```
   Subscription name: "DLA Compliance Test Subscription"
   Resource group: create new or use existing
   Plan: Professional
   ```

3. **Click "Subscribe" button**
   - Microsoft processes payment
   - Redirects to your landing page: `yourapp.azurewebsites.net/marketplace/activate?token=eyJ...`

4. **Landing page should:**
   - Resolve token with Microsoft API
   - Show "Activating subscription..." spinner
   - Create organization in database
   - Activate subscription with Microsoft
   - Redirect to `/marketplace/success?subscription_id=...&plan=professional`

5. **Success page should:**
   - Show green checkmark
   - Display subscription ID
   - Show "Go to Dashboard" button

### 5.3 Test Webhook Events

**Trigger webhook manually:**

```bash
# Test Unsubscribe event
curl -X POST https://yourapp.azurewebsites.net/marketplace/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "id": "test-event-001",
    "subscriptionId": "your-test-subscription-id",
    "action": "Unsubscribe",
    "status": "Unsubscribed",
    "timeStamp": "2024-02-18T10:00:00Z"
  }'

# Test ChangePlan event
curl -X POST https://yourapp.azurewebsites.net/marketplace/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "id": "test-event-002",
    "subscriptionId": "your-test-subscription-id",
    "action": "ChangePlan",
    "planId": "enterprise",
    "status": "Subscribed",
    "timeStamp": "2024-02-18T10:05:00Z"
  }'
```

**Expected behavior:**
- Webhook returns `200 OK`
- Check logs in Azure Functions → Log Stream
- Check database: subscription status updated

### 5.4 Test Subscription Cancellation

1. **In Azure Portal:**
   - Resource Groups → find your test subscription resource
   - Click "Cancel subscription"
   - Confirm cancellation

2. **Within 5 minutes:**
   - Microsoft sends Unsubscribe webhook
   - Your webhook handler updates database
   - Organization should be disabled (read-only)

3. **Verify in database:**
   ```sql
   SELECT * FROM c WHERE c.subscription_id = "your-test-sub-id"
   ```
   Should show: `"status": "Unsubscribed"`

---

## STEP 6: Monitoring & Debugging

### 6.1 Check Microsoft's Webhook Logs

**Partner Center → Marketplace offers → Your offer → Technical configuration → Webhook logs**

Shows:
- All webhook events sent
- Your endpoint's HTTP response codes
- Retry attempts (Microsoft retries 3 times on failure)

### 6.2 Azure Function Logs

```bash
# Stream logs in real-time
az functionapp log tail \
  --name <your-function-app-name> \
  --resource-group <your-resource-group>
```

**Look for:**
- `🛍️ Marketplace landing page accessed`
- `✅ Resolved subscription: <id>`
- `🚀 Activating subscription with Microsoft...`
- `✅ Subscription activated: <id>`
- `📬 Marketplace webhook received`
- `❌ Failed to...` (errors)

### 6.3 Common Issues

**Issue: "Failed to resolve marketplace token"**
- **Cause:** Invalid CLIENT_ID or CLIENT_SECRET
- **Fix:** Verify env vars match App Registration in Azure AD

**Issue: "Failed to activate subscription"**
- **Cause:** Activation called more than once, or > 10 days after subscribe
- **Fix:** Microsoft only allows activation once. Contact support to reset.

**Issue: Webhook never arrives**
- **Cause:** Webhook URL incorrect in Partner Center
- **Fix:** Verify webhook URL is publicly accessible (no auth, returns 200 on POST)

**Issue: Customer stuck on loading screen**
- **Cause:** Landing page redirect failed
- **Fix:** Check APP_URL env var, verify success page exists

---

## STEP 7: Go Live Checklist

Before switching from test to production offer:

### Pre-Launch

- [ ] Test offer fully working (purchase → activate → use → cancel)
- [ ] All webhook events tested (Unsubscribe, ChangePlan, Suspend, Reinstate)
- [ ] Success landing page works
- [ ] Organization provisioning works (creates org + user)
- [ ] Database schema finalized
- [ ] Monitoring/alerting set up (webhook failures, activation failures)
- [ ] Support email configured (appears in marketplace listing)
- [ ] Documentation ready (support.yourcompany.com/marketplace)

### Production Offer Setup

- [ ] Create production offer: `dla-compliance-platform`
- [ ] Set real pricing: £500/mo Professional, £2000/mo Enterprise
- [ ] Upload 5 high-quality screenshots
- [ ] Record 90-second demo video
- [ ] Write compelling description (use template from Part 1)
- [ ] Update technical config with production URLs
- [ ] Submit for Microsoft review (wait 1-2 weeks)

### Post-Approval

- [ ] Test production offer with pilot customer
- [ ] Monitor first 5 subscriptions closely
- [ ] Set up billing alerts (Stripe dashboard for revenue tracking)
- [ ] Create internal runbook for subscription issues
- [ ] Train support team on marketplace-specific questions

---

## STEP 8: Revenue & Billing

### How Microsoft Pays You

1. **Customer subscribes** → Microsoft charges their Azure bill
2. **You get paid** → Monthly deposits to your bank account
3. **Microsoft takes commission:**
   - 3% for SaaS apps (lowest rate)
   - 20% for VM-based apps (not applicable here)

### Revenue Example

```
Customer subscribes to Enterprise plan: $2,400/month
Microsoft commission (3%): -$72
Your payout: $2,328/month
```

**Payment schedule:**
- Billings occur on 1st of month
- Microsoft pays you ~45 days later (NET 45 terms)
- First payment takes 60-90 days (initial verification)

### Tax Implications

- Microsoft handles sales tax/VAT in customer's region
- You receive NET revenue (after Microsoft commission)
- Report as B2B SaaS revenue on your taxes

---

## STEP 9: Customer Onboarding Flow

**Customer journey:**

1. **Discovery** → Search "compliance software" in Azure Marketplace
2. **Product page** → Read your description, watch demo video
3. **Subscribe** → Click "Get It Now", fill subscription form
4. **Activation** → Redirected to your landing page
5. **Sign in** → Use Microsoft account (SSO via Azure AD)
6. **Onboarding** → 3-step wizard:
   - Select jurisdiction (UK, EU, US, ZA)
   - Invite team members
   - Upload first document
7. **First scan** → See AI in action
8. **Value realization** → Compare 3-hour manual review vs 15-minute AI review

---

## STEP 10: Marketing Your Marketplace Listing

### SEO Keywords (for marketplace search)

Include in description:
- "Financial compliance software"
- "FCA compliance automation"
- "SEC marketing review tool"
- "Regulatory document scanning"
- "Legal advisory workflow"
- "DLA Piper approved"

### Social Proof

Add to description:
- "Used by DLA Piper for compliance advisory"
- "Trusted by top 50 UK asset managers"
- "Processes 500+ documents/month for wealth management firms"

### Call to Action

End description with:
- "Start your 14-day free trial today - no credit card required"
- "See results in your first document scan"
- "Used by compliance teams at [big name]"

---

## Support Contact

Questions during setup?
- Email: support@yourcompany.com
- Microsoft Partner Support: https://partner.microsoft.com/support
- Marketplace Publishing Guide: https://docs.microsoft.com/azure/marketplace/

---

**READY TO GO LIVE?**

1. Deploy marketplace.py to Azure Functions
2. Add routes to function_app.py
3. Set environment variables
4. Create Cosmos container
5. Test with test offer
6. Submit production offer
7. Wait for Microsoft approval
8. Launch! 🚀