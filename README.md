# Enterprise Compliance Platform — Developer Documentation

> **Version:** 2.1.0 | **Last updated:** 2025  
> **Clients:** DLA Piper · McKinsey · Investec  
> **Stack:** Azure Functions (Python) + Next.js 16 + Cosmos DB + Azure OpenAI

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Repository Layout](#2-repository-layout)
3. [Environment Variables](#3-environment-variables)
4. [Local Development Setup](#4-local-development-setup)
5. [Authentication & Role System](#5-authentication--role-system)
6. [API Reference — All Endpoints](#6-api-reference--all-endpoints)
7. [Route Ordering Rules](#7-route-ordering-rules)
8. [Data Models](#8-data-models)
9. [Frontend Integration Notes](#9-frontend-integration-notes)
10. [AI Features](#10-ai-features)
11. [Workflow Engine](#11-workflow-engine)
12. [Legal Advisory System](#12-legal-advisory-system)
13. [Discussion System](#13-discussion-system)
14. [Document Detail Features](#14-document-detail-features)
15. [PDF Viewer Fix](#15-pdf-viewer-fix)
16. [Deployment](#16-deployment)
17. [Common Pitfalls & Troubleshooting](#17-common-pitfalls--troubleshooting)
18. [Development Roadmap](#18-development-roadmap)

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  Next.js 16 Frontend (Vercel / Azure Static Web Apps)           │
│  - TailwindCSS dark theme (slate-950 bg, green-400 accents)    │
│  - react-pdf for inline PDF rendering                           │
│  - Microsoft Entra ID MSAL.js for auth                         │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTPS / Bearer JWT
┌────────────────────────────▼────────────────────────────────────┐
│  Azure Functions (Python 3.11)                                  │
│  function_app.py  — ~165 HTTP endpoints                         │
│  function_app_pkg/                                              │
│    api/        — thin handler modules                           │
│    core/       — database, rules engine, cost tracker           │
│    shared/     — http_utils, helpers                            │
└────┬──────────────────────────────────┬───────────────────────┬─┘
     │                                  │                       │
┌────▼──────┐  ┌──────────────────────┐ │  ┌──────────────────┐ │
│ Cosmos DB │  │  Azure Blob Storage  │ │  │  Azure OpenAI    │ │
│ documents │  │  (PDF/DOCX uploads)  │ │  │  GPT-4           │ │
│ audit_logs│  └──────────────────────┘ │  └──────────────────┘ │
│ users     │                           │                       │
└───────────┘          ┌────────────────▼──────────────────────┐ │
                       │  Microsoft Entra ID (Azure AD)         │ │
                       │  JWT validation, app roles             │ │
                       └───────────────────────────────────────┘ │
                                                  ┌──────────────▼┐
                                                  │ Azure AI Search│
                                                  │ (vector search)│
                                                  └───────────────┘
```

---

## 2. Repository Layout

```
project-root/
├── function_app.py                  ← MAIN entry point (all HTTP routes)
├── function_app_pkg/
│   ├── api/
│   │   ├── auth.py                  ← JWT auth, AppRole enum, login handler
│   │   ├── analytics.py             ← Dashboard, compliance score, violations
│   │   ├── advanced_analytics.py    ← User perf, violation trends, SLA
│   │   ├── approval.py              ← Submit/approve/reject/escalate/legal queue
│   │   ├── audit.py                 ← Audit log search + CSV export
│   │   ├── briefing.py              ← AI compliance briefing generator
│   │   ├── certificate.py           ← Certificate generation & verification
│   │   ├── chat.py                  ← Legacy document chat handler
│   │   ├── delete_documents.py      ← Single and batch document deletion
│   │   ├── discussion_handlers.py   ← All discussion CRUD + AI contribution
│   │   ├── document_assignments.py  ← Assignment CRUD + queue + analytics
│   │   ├── document_notifications.py
│   │   ├── generate_questions.py    ← AI questionnaire generation
│   │   ├── get_document.py
│   │   ├── health.py
│   │   ├── jurisdictions.py
│   │   ├── list_documents.py
│   │   ├── ml_export.py
│   │   ├── platform_admin.py
│   │   ├── platform_settings.py
│   │   ├── regulatory_admin.py      ← Browse/search/update regulations
│   │   ├── rescan.py
│   │   ├── scan.py                  ← AI compliance scan
│   │   ├── sla_management.py
│   │   ├── submit_answers.py
│   │   ├── team_collaboration.py    ← Activity feed, watchers, notifications
│   │   ├── team_workload_handler.py
│   │   ├── teams.py                 ← Team CRUD + member management
│   │   ├── upload.py
│   │   ├── user_management.py       ← User CRUD, workload, decisions
│   │   └── workflows.py             ← Workflow template CRUD + stage actions
│   ├── core/
│   │   ├── cost_tracker.py          ← Azure OpenAI cost logging
│   │   ├── custom_rules.py          ← Custom compliance rule engine
│   │   └── database.py              ← Cosmos DB helpers
│   └── shared/
│       └── http_utils.py            ← json_response() helper
├── frontend/                        ← Next.js app (separate deployable)
│   ├── app/(dashboard)/documents/[id]/
│   │   └── page.tsx                 ← Document detail page (tabs)
│   └── components/documents/
│       └── document-viewer.tsx      ← PDF/text inline viewer
├── host.json
├── local.settings.json.example
└── requirements.txt
```

---

## 3. Environment Variables

Copy `local.settings.json.example` to `local.settings.json` for local dev.  
All of these must also be set in Azure Function App Configuration in production.

### Required — Azure

| Variable | Description |
|---|---|
| `AZURE_TENANT_ID` | Entra ID tenant GUID |
| `AZURE_CLIENT_ID` | App registration client ID |
| `AZURE_CLIENT_SECRET` | App registration client secret |
| `COSMOS_DB_ENDPOINT` | `https://<account>.documents.azure.com:443/` |
| `COSMOS_DB_KEY` | Primary key for Cosmos DB |
| `COSMOS_DB_DATABASE` | Database name (e.g. `compliance-platform`) |
| `AZURE_STORAGE_CONNECTION_STRING` | Blob storage for uploaded files |
| `AZURE_STORAGE_CONTAINER` | Blob container name (default: `documents`) |
| `AZURE_STORAGE_ACCOUNT_KEY` | Needed for SAS URL generation |

### Required — Azure OpenAI

| Variable | Description |
|---|---|
| `AZURE_OPENAI_ENDPOINT` | `https://<resource>.openai.azure.com/` |
| `AZURE_OPENAI_API_KEY` | API key |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | Deployment name (e.g. `gpt-4`) |
| `AZURE_OPENAI_API_VERSION` | API version (e.g. `2025-01-01-preview`) |

### Optional

| Variable | Default | Description |
|---|---|---|
| `AZURE_STORAGE_ACCOUNT_KEY` | — | Required for SAS URL generation; falls back to byte stream |
| `AZURE_AI_SEARCH_ENDPOINT` | — | For vector similarity search |
| `AZURE_AI_SEARCH_KEY` | — | — |

---

## 4. Local Development Setup

### Backend (Azure Functions)

```bash
# 1. Install Azure Functions Core Tools v4
npm install -g azure-functions-core-tools@4

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure local settings
cp local.settings.json.example local.settings.json
# → Fill in your Azure credentials

# 5. Start the function host
func start
# API available at http://localhost:7071/api/
```

### Frontend (Next.js)

```bash
cd frontend
npm install

# Create .env.local from example
cp .env.local.example .env.local
# → Set NEXT_PUBLIC_API_URL=http://localhost:7071/api
# → Set NEXT_PUBLIC_AZURE_CLIENT_ID, NEXT_PUBLIC_AZURE_TENANT_ID

npm run dev
# App available at http://localhost:3000
```

### Cosmos DB Emulator (optional)

Download the [Azure Cosmos DB Emulator](https://docs.microsoft.com/azure/cosmos-db/local-emulator).  
Set `COSMOS_DB_ENDPOINT=https://localhost:8081` and use the well-known emulator key.

---

## 5. Authentication & Role System

### How Auth Works

1. Frontend authenticates the user with **Microsoft Entra ID** using MSAL.js.
2. A JWT bearer token is included in every API request: `Authorization: Bearer <token>`.
3. `function_app_pkg/api/auth.py` validates the token signature and claims.
4. The decoded user object is stored in a **thread-local** (`_tls.user`) for the duration of the request.

### Auth Decorators

Every route handler uses one of two decorators:

```python
@require_auth
def my_endpoint(req):
    user = _current_user()          # Returns the authenticated user object
    org_id = user.organization_id   # Always available

@require_role(AppRole.ADMIN, AppRole.SUPER_ADMIN)
def admin_only_endpoint(req):
    user = _current_user()
    # SuperAdmin always passes; others must match at least one listed role
```

### `user_to_dict(user)`

Some legacy handler modules expect a plain `dict` instead of the user object.  
Use the helper:

```python
from function_app import user_to_dict
handle_escalate(req, user_to_dict(_current_user()))
```

### Role Hierarchy

| Role value | Description |
|---|---|
| `Platform.SuperAdmin` | Bypasses all role checks; full platform access |
| `Organization.Admin` | Full org management |
| `Compliance.Officer` | Approve/reject/escalate documents |
| `Legal.Advisor` | Legal queue, advisory decisions |
| `DLAPiper.Advisory` | DLA Piper external legal team |
| `Document.Reviewer` | Assigned document reviewer |

Role values live in `function_app_pkg/api/auth.py` as the `AppRole` enum.

---

## 6. API Reference — All Endpoints

Base URL (production): `https://<function-app>.azurewebsites.net/api`  
Base URL (local): `http://localhost:7071/api`

> 🔓 = No auth required · 🔒 = Any authenticated user · 🛡️ = Role restricted

### 3. Public

| Method | Path | Description |
|---|---|---|
| GET 🔓 | `/health` | Health check |
| POST 🔓 | `/auth/login` | Exchange credentials for JWT |
| GET 🔓 | `/jurisdictions` | List all supported jurisdictions |
| GET 🔓 | `/verify/{certificateId}` | Verify a compliance certificate |

### 4. Auth & Profile

| Method | Path | Auth |
|---|---|---|
| GET | `/auth/me` | 🔒 |
| POST | `/auth/verify` | — |
| GET/PUT | `/users/profile` | 🔒 |

### 5. Documents

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/documents` | 🔒 | Paginated list |
| POST | `/documents/upload` | 🔒 | Multipart form upload |
| POST | `/documents/delete-multiple` | 🔒 | Body: `{ ids: string[] }` |
| GET | `/documents/{id}` | 🔒 | Full document detail |
| DELETE | `/documents/{id}` | 🔒 | |
| PATCH | `/documents/{id}` | 🔒 | Patchable: `violation_resolutions`, `notes`, `tags`, `custom_metadata`, `internal_notes` |
| POST | `/documents/scan/{id}` | 🔒 | Trigger AI compliance scan |
| POST | `/documents/{id}/rescan` | 🔒 | Re-trigger scan |
| GET | `/documents/{id}/ai-chat` | 🔒 | Load chat session |
| POST | `/documents/{id}/ai-chat` | 🔒 | Save chat session |
| DELETE | `/documents/{id}/ai-chat` | 🔒 | Clear chat session |
| POST | `/documents/{id}/ai-chat/message` | 🔒 | Send message, get AI reply |
| POST | `/documents/{id}/chat` | 🔒 | Legacy chat (wraps api/chat.py) |
| POST | `/documents/{id}/generate-questions` | 🔒 | AI questionnaire generation |
| POST | `/documents/{id}/briefing` | 🔒 | AI compliance briefing |
| POST | `/documents/{id}/submit-answers` | 🔒 | Submit questionnaire answers |
| GET | `/documents/{id}/similar` | 🔒 | Similar risk/jurisdiction docs |
| GET | `/documents/{id}/export-report` | 🔒 | Download PDF compliance report |
| GET | `/documents/{id}/file` | 🔒 | Inline file preview (SAS redirect) |
| GET | `/documents/{id}/activity` | 🔒 | Activity feed |
| GET | `/documents/{id}/audit-logs` | 🔒 | Full audit log |
| GET | `/documents/{id}/decision-trail` | 🔒 | All approval decisions |
| GET | `/documents/{id}/ai-conversations` | 🔒 | All AI chat sessions |
| GET | `/documents/{id}/applied-regulations` | 🔒 | Regulations applied during scan |
| GET | `/documents/{id}/notifications` | 🔒 | |
| POST | `/documents/{id}/notifications/mark-read` | 🔒 | |
| POST | `/documents/{id}/assign` | 🛡️ Compliance+ | |
| POST | `/documents/{id}/assign-team` | 🛡️ Compliance+ | |
| POST | `/documents/{id}/submit-review` | 🔒 | |
| POST | `/documents/{id}/approve` | 🛡️ Compliance+ | |
| POST | `/documents/{id}/reject` | 🛡️ Compliance+ | |
| POST | `/documents/{id}/escalate` | 🛡️ Compliance+ | |
| GET | `/documents/{id}/workflow` | 🔒 | |
| POST | `/documents/{id}/workflow/assign` | 🔒 | |
| POST | `/documents/{id}/workflow/advance` | 🛡️ Admin+ | |
| POST | `/documents/{id}/submit-workflow` | 🔒 | |
| POST | `/documents/{id}/approve-stage` | 🔒 | Stage-level approval |
| POST | `/documents/{id}/reject-stage` | 🔒 | Stage-level rejection |
| GET/POST | `/documents/{id}/watchers` | 🔒 | |
| DELETE | `/documents/{id}/watchers/{email}` | 🔒 | |
| POST | `/documents/{id}/generate-certificate` | 🔒 | |
| GET | `/documents/{id}/certificates` | 🔒 | |
| GET | `/documents/{id}/discussions` | 🔒 | |
| POST | `/documents/{id}/discussions` | 🔒 | Create discussion/comment |
| GET | `/documents/{id}/discussions/search-users` | 🔒 | For @mention autocomplete |
| POST | `/documents/{id}/discussions/ai-contribution` | 🔒 | AI posts to discussion |
| POST | `/documents/{id}/discussions/{dId}/reply` | 🔒 | |
| POST | `/documents/{id}/discussions/{dId}/resolve` | 🔒 | |

### 6. Assignments

| Method | Path | Auth |
|---|---|---|
| GET | `/assignments/my-queue` | 🔒 |
| GET | `/assignments/assignment-analytics` | 🛡️ Compliance+ |
| GET/PUT | `/assignments/{id}` | 🔒 |
| GET | `/assignments/{id}/full-context` | 🔒 |
| GET | `/assignments/{id}/timeline` | 🔒 |
| GET | `/assignments/{id}/decisions` | 🔒 |
| POST | `/assignments/{id}/watchers` | 🔒 |
| POST | `/assignments/{id}/comments` | 🔒 |

### 7. Team / Teams

| Method | Path | Auth |
|---|---|---|
| GET | `/team/workload` | 🛡️ Compliance+ |
| GET | `/team/activity` | 🔒 |
| GET | `/team/queue` | 🔒 |
| POST | `/team/queue/{documentId}/claim` | 🔒 |
| GET | `/team/members` | 🔒 |
| GET | `/teams/my-queue` | 🔒 |
| GET/POST | `/teams` | 🔒 / 🛡️ Admin |
| GET | `/teams/{id}` | 🔒 |
| GET | `/teams/{id}/dashboard` | 🔒 |
| POST | `/teams/{id}/members` | 🛡️ Compliance+ |
| DELETE | `/teams/{id}/members/{email}` | 🛡️ Compliance+ |
| PUT | `/teams/{id}/members/{email}/role` | 🛡️ Admin+ |

### 8. Legal

| Method | Path | Auth |
|---|---|---|
| GET | `/legal/queue` | 🛡️ Legal/DLA |
| GET | `/legal/advisory` | 🛡️ Compliance+ |
| GET | `/legal/my-advisories` | 🛡️ Legal/DLA |
| GET | `/legal/history` | 🛡️ Compliance+ |
| POST | `/legal/documents/{id}/approve` | 🛡️ Legal/DLA |
| POST | `/legal/documents/{id}/reject` | 🛡️ Legal/DLA |
| POST | `/legal/documents/{id}/advise` | 🛡️ Legal/DLA |

#### `POST /legal/documents/{id}/advise` body:
```json
{
  "advisory": "string (required)",
  "recommendation": "approve | reject | review",
  "cited_regulations": ["FCA COBS 4.2", "MiFID II Art. 24"]
}
```

### 9. Workflows

| Method | Path | Auth |
|---|---|---|
| GET | `/workflows/pending-approvals` | 🛡️ Compliance+ |
| GET | `/workflows/recommendations` | 🔒 |
| GET/POST | `/workflows` | 🔒 / 🛡️ Admin |
| GET/PUT/DELETE | `/workflows/{id}` | 🔒 / 🛡️ Admin |

### 10. Regulations

| Method | Path | Auth |
|---|---|---|
| GET | `/regulations/lookup?reference=<ref>` | 🔒 |
| GET | `/regulations/search` | 🔒 |
| GET | `/regulations/updates` | 🔒 |
| GET | `/regulations/stats` | 🔒 |
| GET | `/regulations` | 🔒 |
| GET | `/regulations/{id}` | 🔒 |

### 11. Admin User Management

| Method | Path | Auth |
|---|---|---|
| GET | `/manage/users/workload` | 🛡️ Compliance+ |
| POST | `/manage/users/invite` | 🛡️ Admin+ |
| GET | `/manage/users` | 🛡️ Compliance+ |
| GET/PUT/DELETE | `/manage/users/{id}` | 🛡️ Admin+ |
| PUT | `/manage/users/{id}/role` | 🛡️ Admin+ |
| GET | `/manage/users/{id}/activity` | 🛡️ Admin+ |
| GET | `/manage/users/{id}/decisions` | 🛡️ Admin+ |
| GET | `/manage/overview` | 🛡️ Admin+ |
| GET | `/manage/rules` | 🛡️ Compliance+ |
| GET | `/manage/teams` | 🛡️ Admin+ |

### 12–18. Other Sections

| Section | Base path | Auth |
|---|---|---|
| Certificates | `/certificates`, `/certificates/{id}` | 🔒 |
| Notifications | `/notifications` | 🔒 |
| Analytics | `/analytics/*` | 🔒 / 🛡️ varies |
| Audit | `/audit/search`, `/audit/export` | 🔒 |
| Custom Rules | `/custom-rules` | 🛡️ varies |
| SLA | `/settings/sla`, `/sla/dashboard` | 🛡️ varies |
| Platform Admin | `/platform/*`, `/ml/training-data` | 🛡️ SuperAdmin |

---

## 7. Route Ordering Rules

**This is the most common cause of 404s and routing bugs in Azure Functions.**

Azure Functions resolves routes top-to-bottom in registration order. **Literal paths must always be registered before parameterised paths at the same depth.**

```
✅ CORRECT ORDER:
  /regulations/lookup          ← registered first (literal)
  /regulations/search          ← registered second (literal)
  /regulations/{regulationId}  ← registered last (parameter)

❌ WRONG ORDER:
  /regulations/{regulationId}  ← catches "lookup" and "search" too!
  /regulations/lookup
```

The same rule applies across all sections in `function_app.py`:

| Section | Literals before params |
|---|---|
| Documents | `upload`, `delete-multiple` before `{documentId}` |
| Assignments | `my-queue`, `assignment-analytics` before `{assignmentId}` |
| Teams | `my-queue` before `{teamId}` |
| Regulations | `lookup`, `search`, `updates`, `stats` before `{regulationId}` |
| Manage/Users | `workload`, `invite` before `{userId}` |
| Workflows | `pending-approvals`, `recommendations` before `{workflowId}` |
| Notifications | `mark-all-read` before `{notificationId}/read` |

---

## 8. Data Models

### Document (Cosmos DB `documents` container)

```typescript
interface Document {
  id: string;
  type: "document";
  organization_id: string;          // partition key

  // File info
  filename: string;
  blob_path?: string;
  blob_url?: string;
  file_size?: number;
  content_type?: string;

  // Scan results
  status: "pending" | "scanning" | "review" | "approved" | "rejected" | "escalated";
  compliance_outcome: "compliant" | "non_compliant" | "requires_review" | null;
  risk_score: number;                // 0–100
  violations: Violation[];
  violations_count: number;
  violation_resolutions: Record<string, ViolationResolution>;
  recommendations: string[];
  jurisdiction: string;
  applied_regulations: string[];

  // Workflow
  workflow_id?: string;
  workflow_status?: string;
  current_stage?: number;

  // Assignment
  assigned_to?: string;              // user email
  assigned_to_name?: string;
  assigned_at?: string;
  assignment_priority?: "low" | "medium" | "high" | "urgent";
  assignment_deadline?: string;
  team_name?: string;
  ticket_id?: string;

  // Legal
  legal_advisory?: string;
  legal_recommendation?: "approve" | "reject" | "review";
  legal_reviewed_by?: string;
  legal_reviewed_at?: string;
  cited_regulations?: string[];

  // Approval
  approved_at?: string;
  approved_by?: string;
  rejected_at?: string;
  rejected_by?: string;
  escalated_at?: string;
  escalated_by?: string;
  escalation_reason?: string;

  // Metadata
  uploaded_by: string;
  uploaded_by_name?: string;
  created_at: string;
  updated_at: string;
  tags?: string[];
  notes?: string;
  internal_notes?: string;
  custom_metadata?: Record<string, unknown>;
  certificates?: Certificate[];
}
```

### Violation

```typescript
interface Violation {
  id: string;
  category: string;
  description: string;
  severity: "low" | "medium" | "high" | "critical";
  remediation: string;
  regulation_reference?: string;
  source?: "ai" | "custom_rule";
  rule_id?: string;                // if source = custom_rule
}
```

### ViolationResolution

```typescript
interface ViolationResolution {
  status: "unresolved" | "addressed" | "waived" | "in_progress";
  note?: string;
  resolved_by?: string;
  resolved_at?: string;
}
```

### Discussion

```typescript
interface Discussion {
  id: string;
  type: "discussion";
  organization_id: string;
  document_id: string;

  content: string;
  author: string;                   // user email
  author_name?: string;
  author_type: "human" | "ai";
  is_ai?: boolean;                  // true for AI-generated posts

  // Threading
  parent_id?: string;               // set for replies
  replies?: Discussion[];

  is_resolved?: boolean;
  resolved_by?: string;
  resolved_at?: string;

  // @mentions
  mentions?: string[];              // list of mentioned user emails

  created_at: string;
  updated_at?: string;
}
```

### Legal Advisory (embedded in Document)

A legal advisory is stored directly on the document record (not as a separate entity).  
Decision trail entries are additionally saved to the `audit_logs` container.

### Decision Trail Entry

```typescript
interface DecisionTrailEntry {
  id: string;
  type: "decision_trail";
  organization_id: string;
  document_id: string;
  document_filename: string;

  decision: string;                 // e.g. "approve", "reject", "advisory"
  decision_type: string;            // e.g. "legal_advisory"
  decision_maker: {
    email: string;
    name: string;
    roles: string[];
  };
  decision_context: {
    notes?: string;
    reason?: string;
    advisory?: string;
    recommendation?: string;
    cited_regulations?: string[];
  };
  decision_timestamp: string;
  created_at: string;
}
```

---

## 9. Frontend Integration Notes

### API Client Pattern

The frontend uses a central `apiClient` (typically in `lib/api.ts`) that:
1. Reads the MSAL access token from session storage.
2. Attaches it as `Authorization: Bearer <token>`.
3. Handles 401 responses by triggering a silent token refresh.

```typescript
async function apiFetch(path: string, options?: RequestInit) {
  const token = await getAccessToken();   // MSAL silent acquire
  return fetch(`${process.env.NEXT_PUBLIC_API_URL}/${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${token}`,
      ...options?.headers,
    },
  });
}
```

### Key Frontend Pages

| Page | Route | Key API calls |
|---|---|---|
| Dashboard | `/dashboard` | `GET /analytics/dashboard` |
| Document list | `/documents` | `GET /documents` |
| Document detail | `/documents/[id]` | `GET /documents/{id}`, all sub-resources |
| Legal queue | `/legal` | `GET /legal/queue`, `GET /legal/advisory` |
| Assignments | `/assignments` | `GET /assignments/my-queue` |
| Admin | `/admin` | `GET /manage/users`, `GET /manage/teams` |
| Analytics | `/analytics` | `GET /analytics/dashboard` |

### Document Detail Tabs

The `/documents/[id]` page has five tabs:

| Tab | Data source |
|---|---|
| **Overview** | `GET /documents/{id}` |
| **Violations** | Embedded in document; PATCH to update resolutions |
| **Discussion** | `GET /documents/{id}/discussions` + AI contribution |
| **Workflow** | `GET /documents/{id}/workflow` + stage actions |
| **Audit** | `GET /documents/{id}/audit-logs` + decision trail |

### Discussion Tab — AI Button

When the user clicks **"Ask AI"** in the discussion input:

1. Frontend collects last 6 discussion messages + top 5 violations + legal advisory from document state.
2. Calls `POST /documents/{id}/discussions/ai-contribution` with that context.
3. The backend calls Azure OpenAI, then saves the response as a new discussion with `is_ai: true`.
4. Frontend re-fetches discussions and renders the AI card with green styling.

### Legal Advisory Card

If `document.legal_advisory` is present, a pinned card is rendered at the top of the Discussions tab:
- Purple left border, Gavel icon
- Recommendation badge (green=approve, red=reject, amber=review)
- Cited regulations as blue pill badges
- This card is not dismissible and counts as +1 in the tab badge

---

## 10. AI Features

### Document Compliance Scan (`POST /documents/scan/{id}`)

1. Fetches document content from Blob Storage.
2. Builds a structured prompt including jurisdiction and custom rules.
3. Calls Azure OpenAI GPT-4 with a compliance analysis system prompt.
4. Parses the response into `violations[]`, `risk_score`, `compliance_outcome`.
5. Saves to Cosmos DB and logs cost to `cost_events`.

### AI Chat (`POST /documents/{id}/ai-chat/message`)

Stateless per-message endpoint. Pass `history` array for context continuity.  
The system prompt is built fresh each call with live document data (violations, risk score, legal advisory).

### AI Discussion Contribution (`POST /documents/{id}/discussions/ai-contribution`)

Used by the "Ask AI" button in the Discussion tab.

**Request body:**
```json
{
  "context": {
    "recent_messages": [...],
    "violations": [...],
    "legal_advisory": "..."
  }
}
```

**What the backend does:**
1. Builds a compliance guidance prompt from the context.
2. Calls `POST /documents/{id}/chat` with the constructed prompt.
3. Creates a new discussion record with `is_ai: true`, `author_type: "ai"`.

### Regulation Lookup (`GET /regulations/lookup?reference=FCA+COBS+4.2`)

1. Searches the `regulations` Cosmos container first.
2. If no match, falls back to Azure OpenAI for an AI-generated explanation.
3. Caches the AI response back to Cosmos for future requests.

### AI Compliance Briefing (`POST /documents/{id}/briefing`)

Generates an executive-level compliance briefing PDF. Response is stored on the document as `doc.briefing`.

---

## 11. Workflow Engine

Workflows are multi-stage approval pipelines. They are created by admins as templates and then assigned to documents.

### Workflow Lifecycle

```
Document uploaded
      ↓
POST /documents/{id}/workflow/assign   ← attach workflow template
      ↓
POST /documents/{id}/submit-workflow   ← move to stage 1
      ↓
POST /documents/{id}/approve-stage     ← advance stage
  or POST /documents/{id}/reject-stage ← send back / block
      ↓
... (repeat per stage)
      ↓
Final stage approved → document.status = "approved"
```

### Creating a Workflow Template

`POST /workflows`

```json
{
  "name": "Standard DLA Piper Review",
  "stages": [
    { "name": "Compliance Review",  "assignee_role": "Compliance.Officer", "sla_hours": 48 },
    { "name": "Legal Advisory",     "assignee_role": "Legal.Advisor",       "sla_hours": 72 },
    { "name": "Partner Sign-off",   "assignee_role": "Organization.Admin",  "sla_hours": 24 }
  ]
}
```

### Manual Override

Admins can force-advance a workflow stage via:
`POST /documents/{id}/workflow/advance`  (requires `Admin` or `SuperAdmin` role)

---

## 12. Legal Advisory System

### Flow

```
Compliance officer escalates document
      ↓
Document appears in GET /legal/queue
      ↓
Legal advisor calls POST /legal/documents/{id}/advise
  body: { advisory, recommendation, cited_regulations }
      ↓
Decision saved to audit_logs (type=decision_trail)
Document updated: legal_advisory, legal_recommendation, workflow_status="legal_reviewed"
      ↓
Advisory visible on document detail → pinned Discussion card
Legal advisor can review history at GET /legal/my-advisories
```

### Recommendations

| Value | Meaning |
|---|---|
| `approve` | Legal clears the document |
| `reject` | Legal recommends rejection |
| `review` | Further compliance review needed |

---

## 13. Discussion System

### Creating a Discussion

`POST /documents/{id}/discussions`

```json
{
  "content": "This clause may conflict with FCA COBS 4.2.1",
  "mentions": ["alice@firm.com"]
}
```

### Creating a Reply

`POST /documents/{id}/discussions/{discussionId}/reply`

```json
{
  "content": "Agreed, I've flagged it for legal review"
}
```

### AI Discussion Card

AI-generated messages have `is_ai: true` and `author_type: "ai"`.  
Frontend renders them with green styling and a Bot icon.

### @mentions

The `mentions` array triggers email notifications to the listed users.  
Use `GET /documents/{id}/discussions/search-users?q=alice` for autocomplete.

---

## 14. Document Detail Features

### Violation Checklist (PATCH `/documents/{id}`)

Track resolution status per violation:

```json
{
  "violation_resolutions": {
    "violation-id-123": {
      "status": "addressed",
      "note": "Added required disclosure on page 3",
      "resolved_by": "user@firm.com",
      "resolved_at": "2025-06-01T10:00:00Z"
    }
  }
}
```

Status values: `unresolved` · `in_progress` · `addressed` · `waived`

### Similar Documents (`GET /documents/{id}/similar`)

Returns documents with the same jurisdiction and a similar risk score (±15 points by default).  
Query params:
- `limit` (default: 5)
- `risk_margin` (default: 15)

### Regulation Lookup (inline)

Frontend calls `GET /regulations/lookup?reference=<ref>` when a user clicks a cited regulation.  
The response includes full regulation text (from DB or AI-generated).

### PDF Report Export (`GET /documents/{id}/export-report`)

Generates a professional PDF using ReportLab. Includes:
- Document metadata table
- Legal advisory section (if present)
- Full violations list with resolution status
- Decision trail
- AI recommendations

**Requires:** `pip install reportlab`

### File Preview (`GET /documents/{id}/file`)

Serves the original uploaded document for inline browser rendering.  
- If `blob_url` is stored: 302 redirect to URL.
- If `AZURE_STORAGE_ACCOUNT_KEY` is configured: generates a 1-hour SAS URL redirect.
- Fallback: streams bytes directly (slower, use only for development).

### Activity Feed (`GET /documents/{id}/activity`)

Returns all activity events for a document: scans, assignments, status changes, comments.  
Default: last 90 days, max 100 items. Accepts `days` and `limit` query params.

### Audit Logs (`GET /documents/{id}/audit-logs`)

Full tamper-evident audit record. Every user action is logged with IP, timestamp, user identity.

---

## 15. PDF Viewer Fix

The frontend PDF viewer (`components/documents/document-viewer.tsx`) had a CDN worker crash.

**Root cause:** `pdfjs.version` returns `5.4.296` which doesn't exist on unpkg/cdnjs.

**Fix applied:**

```tsx
// ✅ Uses the worker bundled in node_modules — no CDN dependency
if (typeof window !== 'undefined') {
  pdfjs.GlobalWorkerOptions.workerSrc = new URL(
    'pdfjs-dist/build/pdf.worker.min.mjs',
    import.meta.url,
  ).toString();
}
```

**Additional viewer improvements:**
- Default scale increased from 1.0 → 1.2 for readability
- Zoom range: 0.5× → 2.5× (step 0.05)
- Graceful fallback to text view on PDF load error with retry button
- Loading state uses `opacity-0` + absolute positioning (no layout shift)
- Quick-jump pagination for documents > 10 pages

---

## 16. Deployment

### Backend — Azure Functions

```bash
# Build and deploy via Azure CLI
az login
func azure functionapp publish <your-function-app-name>

# Or use GitHub Actions (see .github/workflows/deploy-backend.yml)
```

**Azure Function App settings to configure:**
- All environment variables from Section 3
- Runtime: Python 3.11
- Plan: Consumption or Premium (Premium recommended for consistent cold start)
- CORS: Add your frontend domain

### Frontend — Next.js

```bash
# Vercel (recommended)
vercel deploy --prod

# Or Azure Static Web Apps
az staticwebapp create ...
```

**Required environment variables for frontend build:**

```
NEXT_PUBLIC_API_URL=https://<func-app>.azurewebsites.net/api
NEXT_PUBLIC_AZURE_CLIENT_ID=<entra-app-client-id>
NEXT_PUBLIC_AZURE_TENANT_ID=<entra-tenant-id>
NEXT_PUBLIC_AZURE_REDIRECT_URI=https://<your-frontend-domain>
```

### Cosmos DB Setup

Create the following containers (all with `/organization_id` as partition key):

| Container | Description |
|---|---|
| `documents` | Documents, AI chat sessions, cost events |
| `audit_logs` | Decision trail, activity feed, audit logs |
| `users` | User profiles and roles |
| `regulations` | Regulation text cache |

---

## 17. Common Pitfalls & Troubleshooting

### ❌ 404 on a literal route (e.g. `/regulations/lookup`)

**Cause:** A parameterised route (`/regulations/{id}`) is registered before the literal.  
**Fix:** Check section ordering in `function_app.py`. Literals always first.

### ❌ "Setting up fake worker failed" in PDF viewer

**Cause:** CDN version mismatch for pdfjs worker.  
**Fix:** See Section 15 — use the local bundled worker via `import.meta.url`.

### ❌ `handler_func` expects dict, got User object

**Cause:** Some legacy handlers in `api/` were written to accept `dict` users.  
**Fix:** Wrap with `user_to_dict(_current_user())` before passing.

### ❌ Azure OpenAI timeout on document scan

**Cause:** Large documents with many violations can exceed the 30s default.  
**Fix:** Increase `timeout=30.0` in the OpenAI client call in `api/scan.py`. Consider chunking large documents.

### ❌ Cosmos DB 429 (Too Many Requests)

**Cause:** Burst of scan requests consuming all RU/s.  
**Fix:** Increase provisioned throughput on the `documents` container, or implement a queue with Azure Service Bus.

### ❌ PDF export fails with `ModuleNotFoundError: reportlab`

**Fix:** Add `reportlab` to `requirements.txt` and redeploy.

### ❌ `/documents/{id}/file` returns 404 but file exists in Storage

**Cause:** `AZURE_STORAGE_ACCOUNT_KEY` not set, and direct SAS URL generation fails.  
**Fix:** Ensure `AZURE_STORAGE_ACCOUNT_KEY` is in app settings. Alternatively store `blob_url` at upload time.

---

## 18. Development Roadmap

### Planned Features

- [ ] **Bulk AI scan** — scan multiple documents in a single queue operation (Azure Service Bus)
- [ ] **Email notifications** — SendGrid integration for assignment and mention alerts
- [ ] **Mobile app** — React Native wrapper for reviewer approvals
- [ ] **Regulation database** — Seed Cosmos with FCA, MiFID II, GDPR regulation text
- [ ] **Vector similarity search** — Azure AI Search integration for "find similar violations"
- [ ] **Scheduled re-scan** — Timer trigger to re-scan documents when regulations change
- [ ] **Webhook outbound** — Notify external systems (Jira, ServiceNow) on document status change

### Known Technical Debt

- The `chat_with_document_ai` and `escalate_document` handlers both call `user_to_dict()` — the underlying `api/chat.py` and `api/approval.py` handlers should be updated to accept the user object directly.
- `asyncio.run()` in `submit_questionnaire_answers` is a blocking call. Should be refactored to a native async Azure Function when upgrading to the v2 programming model.
- Custom rules engine (`core/custom_rules.py`) stores rules in-memory. Needs a Cosmos-backed persistence layer for multi-instance deployments.

---

*For questions, contact the platform team or raise a PR. All endpoint changes should be reflected in this document.*# greenlight-backend
