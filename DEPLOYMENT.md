# FirstService Unified Risk Dashboard — Deploy Guide

One container. Pull risk data from each brand's Drata workspace. One dashboard: heatmaps, exports, history, board reports. Push edits back to Drata.

This guide: Drata API keys needed (one per brand, up to 13). Where store them. How stand up on Azure. Works on **Azure Container Apps** or **App Service for Containers** — pick one, team already run.

---

## 1. Need this

- Azure subscription, permission make: container host (Container Apps or App Service), Key Vault, small storage account (Azure Files), Entra ID sign-in setup.
- Admin access each FirstService brand's Drata workspace — make one API key per brand (up to 13 total).
- This repo (Dockerfile + `app/` already here — no build tool needed beyond Docker, and even that optional, deploy straight from source, see §5).

---

## 2. Make one Drata API key per brand (up to 13)

Drata no cross-tenant role span 13 brand workspaces. Dashboard auth each one own key.

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
7. Repeat every brand workspace want in dashboard.

Brand's Drata workspace hosted EU or APAC, not US — note that too. Need it step 4 below.

---

## 3. Store keys in Azure Key Vault

App never store raw keys own config — reads from Key Vault at pull-time, uses container's managed identity. No secret ever sit in config file or env var.

1. Make Key Vault (or reuse one team already got).
2. Each brand, add secret — **value** = API key from step 2. Short, URL-safe **name**, e.g.:

   | Secret name | Brand |
   |---|---|
   | `california-closets` | California Closets |
   | `paul-davis-restoration` | Paul Davis Restoration |
   | `firstservice-residential` | FirstService Residential |
   | ... | (up to 13) |

3. Grant app's managed identity read access step 5 below — nothing do here yet, just need vault + secrets in place.

---

## 4. Tell app which 13 tenants pull

App reads small manifest — tenant names + Key Vault secret names, **no actual keys** — knows which brands pull. File safe keep around (no secrets in it), but kept out git by default since environment-specific.

1. Copy `tokens.azure.example.json` → `tokens.azure.json`.
2. Add one entry per brand, match secret names from step 3:

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

Where file live at runtime — covered step 5.4 below.

---

## 5. Deploy container

### 5.1 Make the app (Azure Container Apps)

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

### 5.2 Give app managed identity, let it read vault

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

### 5.3 Add persistent storage (cache + audit log survive restart)

App keep small SQLite file (cached risk data + audit/history log) at `/app/data`. No persistent mount — resets every restart or scale event. Follow Microsoft's [Azure Files volume mount tutorial](https://learn.microsoft.com/en-us/azure/container-apps/storage-mounts-azure-files), make small file share, mount at `/app/data`.

While file share open, also upload `tokens.azure.json` from step 4 into it (Storage Explorer, or `az storage file upload --share-name <share> --source tokens.azure.json --path tokens.azure.json`) — no need bake into image.

### 5.4 Set env vars

On container app, set:

| Variable | Value |
|---|---|
| `AZURE_VAULT_URL` | `https://<your-vault-name>.vault.azure.net/` |
| `TOKENS_PATH` | `/app/data/tokens.azure.json` (override image default, reads mounted share instead) |

`TOKENS_MODE=azure` and `DB_PATH=/app/data/app.db` already set by Dockerfile — no repeat need.

### 5.5 Turn on sign-in (Entra ID / Easy Auth)

Container app's **Authentication** blade (same feature App Service, old name "Easy Auth"): add identity provider → Microsoft Entra ID → point at FirstService tenant. App reads whatever identity this inject — no login code of own configure.

---

## 6. First run

1. Open app URL, sign in.
2. Click **Pull Latest** on dashboard — fetch every configured brand's registers, risks, users.
3. From there: view heatmaps, edit risks, **Push Data to Drata** sync edits back, export (PNG/PDF/CSV/XLSX), compare snapshots over time, generate one-click board reports.

---

## Run local first (recommend before touch Azure)

Good way sanity-check Drata API keys before wire up Key Vault:

```bash
git clone <this repo>
cd UnifiedRiskDashboard
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp tokens.example.json tokens.json   # paste one or more real keys directly — local mode only
uvicorn app.main:app --reload
```

Visit `http://localhost:8000`, click **Pull Latest**, confirm key(s) work. `tokens.json` (raw keys, local mode) gitignored, never touch Key Vault — purely this kind local check.

---

## Questions

Rodrigo Villasenor / Brandan Tottle (Drata).
