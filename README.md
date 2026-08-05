# FirstService Unified Risk Dashboard

One container. Pull risk data from each brand's Drata workspace. One dashboard: heatmaps, exports, history, board reports. Push edits back to Drata. Runs on **Azure Container Apps** or **App Service for Containers** — pick one, either work same.

---

## Quick start (local, no Azure needed)

Fastest way check it work before touch Azure:

```bash
git clone <this repo>
cd UnifiedRiskDashboard
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp tokens.example.json tokens.json   # paste one or more real Drata API keys directly — local mode only
uvicorn app.main:app --reload
```

Visit `http://localhost:8000`, click **Pull Latest**, confirm key(s) work. `tokens.json` (raw keys, local mode) gitignored, never touch Key Vault — purely local check.

---

## Full setup (production, up to 13 tenants)

Three parts: get Drata keys, store them safe, deploy container.

### 1. Make one Drata API key per brand (up to 13)

Drata got no cross-tenant role span 13 brand workspaces. Dashboard auth each one own key.

**Each brand's Drata workspace:**

1. Log in that brand's Drata as Account Owner/Admin.
2. Account menu (top right) → **Settings** → **API Keys** → **Create API Key**.
3. Name it something recognizable, e.g. `unified-risk-dashboard`.
4. Expiration: 12 months Drata default. Whatever pick, set calendar reminder — app no auto-rotate keys.
5. **Scopes**: pick **Custom**, select only these four (read three, write one — app never create or delete anything Drata):

   | Permission | Why app need it |
   |---|---|
   | Risk Management: List Risk Registers | Find each brand's risk register(s) |
   | Risk Management: List Risks | Pull risks into dashboard |
   | Risk Management: Update Risk | Push edited fields (impact, likelihood, status, treatment, title, description, owner) back to Drata |
   | Users: List Users | Fill "assign owner" dropdown with real people from workspace |

   *(Exact checkbox words maybe vary by Drata UI version — match by name. These four = only Drata endpoints app calls, one-to-one.)*
6. Click **Done**, copy key now — **Drata show it once only.**
7. Repeat every brand workspace want in dashboard. Brand hosted EU or APAC not US — note that too, need it in step 3 below.

### 2. Store keys in Azure Key Vault

App never store raw keys own config — reads from Key Vault at pull-time via container's managed identity. No secret ever sit in config file or env var.

1. Make Key Vault (or reuse one team already got).
2. Each brand, add secret — **value** = API key from step 1. Short, URL-safe **name**, e.g.:

   | Secret name | Brand |
   |---|---|
   | `california-closets` | California Closets |
   | `paul-davis-restoration` | Paul Davis Restoration |
   | `firstservice-residential` | FirstService Residential |
   | ... | (up to 13) |

### 3. Tell app which tenants to pull

App reads small manifest — tenant names + Key Vault secret names, **no actual keys** — knows which brands pull. No secrets in this file, but kept out git by default since environment-specific.

1. Copy `tokens.azure.example.json` → `tokens.azure.json`.
2. Add one entry per brand, match secret names from step 2:

   ```json
   {
     "tenants": [
       { "name": "California Closets",         "secret_name": "california-closets" },
       { "name": "Paul Davis Restoration",      "secret_name": "paul-davis-restoration" },
       { "name": "FirstService Residential",    "secret_name": "firstservice-residential" }
       // ... up to 13 entries total
     ]
   }
   ```

3. Only add `"region": "eu"` or `"region": "apac"` if that brand's Drata workspace hosted outside US — omit otherwise (default US).

### 4. Deploy the container

From folder with Dockerfile:

```bash
az extension add --name containerapp --upgrade
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.OperationalInsights

az containerapp up \
  --name firstservice-risk-dashboard \
  --resource-group <your-resource-group> \
  --location <your-region> \
  --environment firstservice-risk-env \
  --source .
```

`--source .` build image from Dockerfile in cloud — no local Docker install need. (App Service for Containers same idea: build/push image, point Web App for Containers at it, same env vars + mounts below.)

Give app managed identity, let it read the vault:

```bash
az containerapp identity assign \
  --name firstservice-risk-dashboard \
  --resource-group <your-resource-group> \
  --system-assigned

PRINCIPAL_ID=$(az containerapp identity show \
  --name firstservice-risk-dashboard \
  --resource-group <your-resource-group> \
  --query principalId -o tsv)

az role assignment create \
  --assignee "$PRINCIPAL_ID" \
  --role "Key Vault Secrets User" \
  --scope <your-key-vault-resource-id>
```

Add persistent storage — app keep small SQLite file (cached risk data + audit/history log) at `/app/data`. No persistent mount, resets every restart. Follow Microsoft's [Azure Files volume mount tutorial](https://learn.microsoft.com/en-us/azure/container-apps/storage-mounts-azure-files), make small file share, mount at `/app/data`. While share open, upload `tokens.azure.json` from step 3 into it too (Storage Explorer, or `az storage file upload --share-name <share> --source tokens.azure.json --path tokens.azure.json`) — no need bake into image.

Set env vars on the container app:

| Variable | Value |
|---|---|
| `AZURE_VAULT_URL` | `https://<your-vault-name>.vault.azure.net/` |
| `TOKENS_PATH` | `/app/data/tokens.azure.json` (override image default, reads mounted share instead) |

`TOKENS_MODE=azure` and `DB_PATH=/app/data/app.db` already set by Dockerfile — no repeat need.

Turn on sign-in: container app's **Authentication** blade (same feature App Service, old name "Easy Auth") — add identity provider → Microsoft Entra ID → point at FirstService tenant. App reads whatever identity this inject — no login code of own configure.

### 5. First run

1. Open app URL, sign in.
2. Click **Pull Latest** — fetch every configured brand's registers, risks, users.
3. From there: view heatmaps, edit risks, **Push Data to Drata** sync edits back, export (PNG/PDF/CSV/XLSX), compare snapshots over time, generate one-click board reports.

---

## Questions

Rodrigo Villasenor / Brandan Tottle (Drata).
