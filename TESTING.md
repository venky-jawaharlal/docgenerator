# Testing generated Swagger / OpenAPI documents

This guide is for documents produced by `swagger_gen`. The important rule:

> **Try-it-out and any live check must hit the deployed service (`servers` /
> `host` in config), not localhost, and must send the same auth the instance
> expects.** The generator writes that host and security scheme into the spec
> and will not invent a loopback default.

The sample `config.example.yaml` uses fictional hosts such as
`https://users.internal.example.com`. Substitute the real Cloud Run / GKE /
API-gateway URLs for your services before following the live steps below.

## 1. Generate the documents

From the repository root:

```bash
python -m swagger_gen --config config.example.yaml --validate
```

You should see one `[ok]` line per service, including `host: https://…`.
`--validate` fails the run if a spec is missing a non-localhost server.

Ad-hoc local path:

```bash
python -m swagger_gen \
  --repo ./examples/sample-repos/flask-user-service \
  --server https://users.internal.example.com \
  -o ./swagger-output
```

Ad-hoc git URL (requires `git` and network):

```bash
python -m swagger_gen \
  --repo https://github.com/org/your-service.git \
  --ref main \
  --server https://your-service.example.com \
  --validate \
  -o ./swagger-output
```

Output (default `./swagger-output/`):

| File | Purpose |
| --- | --- |
| `index.html` | Catalog of every service and its deployment host |
| `<service>.html` | Interactive Swagger UI for that service |
| `<service>.openapi.yaml` / `.json` | Machine-readable spec |
| `combined.html` + `combined.openapi.*` | All services in one document |

## 2. Open Swagger UI

Do **not** rely on a `file://` URL for Try-it-out. Browsers treat that origin
as opaque, and many APIs reject it. Serve the folder over HTTP:

```bash
python -m http.server 8765 --directory swagger-output
```

Then open:

- http://127.0.0.1:8765/ — index
- http://127.0.0.1:8765/user-service.html — one service
- http://127.0.0.1:8765/combined.html — everything

Serving the UI from localhost is fine. The **requests** still go to the host
listed in the spec (`servers` / the scheme+host dropdown at the top of Swagger
UI), not to `127.0.0.1:8765`.

Confirm before clicking **Try it out**:

1. The dropdown next to the scheme shows your deployed URL
   (e.g. `https://users.internal.example.com`), not `http://localhost:…`.
2. Expanding an operation preview (right-hand side) starts with that same host.

If the dropdown is missing, the spec has no `servers` entry — add `host` or
`--server` and regenerate.

## 3. Authenticate, then execute an operation (Try it out)

Configure `auth` next to `host` so the spec knows *how* the live instance
protects itself:

```yaml
  - name: orders-service
    path: ./examples/sample-repos/fastapi-orders
    host: https://orders.internal.example.com
    auth:
      type: oauth2                 # bearer | basic | apikey | header | oauth2
      grant: password
      token_url: https://orders.internal.example.com/token
      username_env: ORDERS_USER
      password_env: ORDERS_PASSWORD
      apply_to: detected           # or all / none
      probe_path: /health
```

```bash
export ORDERS_USER='alice'
export ORDERS_PASSWORD='…'
export USER_SERVICE_USER='alice'
export USER_SERVICE_PASSWORD='…'
export CATALOG_API_KEY='…'
export BQ_EXPORT_TOKEN='…'

python -m swagger_gen --config config.yaml --validate --check-api
```

1. Open the per-service HTML (preferred over `combined.html` when paths overlap).
2. Authorize so Try-it-out can reach the real instance:
   - Click **Authorize** (lock icon). The dialog lists the scheme from config
     (`bearerAuth`, `basicAuth`, `oauth2`, or your API key header).
   - **Bearer / JWT:** paste the raw token (no `Bearer ` prefix).
   - **Basic:** username and password for the deployed service.
   - **OAuth2:** if the spec includes a token URL, use the OAuth dialog, or
     paste an access token into **bearerAuth**. `--check-api` fetches the
     token for you from `token_url`.
   - **API key:** the key value; the header name is already in the spec.
   - Or generate with `--embed-auth` after exporting the env vars so the HTML
     is pre-authorized. Do **not** copy that HTML to a shared portal.
   - Credentials are **not** stored in `localStorage` (re-authorize if you
     reload). Do not paste production secrets on a shared workstation.
   - For session-cookie APIs with no `auth:` override: Try-it-out from the
     browser only works if you are already logged in on that host. Use curl
     or switch the repo to `auth: { type: bearer, … }` if the deployed
     instance uses a token.
3. Expand an operation → **Try it out** → fill parameters → **Execute**.
4. Check the **Request URL** in the response panel. It must be
   `{deployment-host}{path}`, for example
   `https://orders.internal.example.com/health`.
5. Status `2xx`/`4xx` from that host means the spec is talking to the right
   place (`4xx` is still a successful *routing* test). `Failed to fetch` is
   almost always CORS or a wrong host — see [Troubleshooting](#troubleshooting).

Suggested first calls on the sample repos (against *your* deployed stand-ins):

| Service | Method | Path | Notes |
| --- | --- | --- | --- |
| user-service | `GET` | `/users` | Optional query `role` |
| orders-service | `GET` | `/health` | Unauthenticated |
| orders-service | `GET` | `/orders` | Query `status`, `limit` |
| product-catalog | `GET` | `/api/products` | Query `category`, `page` |
| bigquery-export | `GET` | `/exports/{jobId}` | Needs a real job id + bearer token |

## 4. Replay the same call with curl

Copy **Curl** from the Swagger UI response panel, or build it from the YAML:

```bash
# Health — no auth
curl -sS -D- -o /tmp/body.txt \
  https://orders.internal.example.com/health

# Query + header auth
curl -sS -D- \
  -H "Authorization: Bearer $TOKEN" \
  "https://orders.internal.example.com/orders?status=open&limit=5"

# JSON body
curl -sS -D- -X POST \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"product_id": 1, "quantity": 2}' \
  https://orders.internal.example.com/orders
```

curl is the right tool when the browser is blocked by CORS: it still proves
the generated path, method, and host are correct.

## 5. Validate the spec (no live traffic)

`--validate` already checks OpenAPI 3 structure and that a non-localhost
server is present. To inspect the files yourself:

```bash
# Server list must be the deployed host
python - <<'PY'
import sys, yaml
from pathlib import Path
for p in Path("swagger-output").glob("*.openapi.yaml"):
    doc = yaml.safe_load(p.read_text())
    print(p.name, "->", [s.get("url") for s in doc.get("servers") or []])
PY
```

Optional third-party validators (not required by this project):

```bash
# Node
npx @redocly/cli lint swagger-output/user-service.openapi.yaml

# Python
pip install openapi-spec-validator
python -c "from openapi_spec_validator import validate_file; validate_file('swagger-output/user-service.openapi.yaml')"
```

Paste YAML into https://editor.swagger.io for a visual check. If the editor
offers Try-it-out, it will also use the spec's `servers` — again, that must
be the deployed host.

## 6. Ping and authenticate against the deployment host

```bash
# Reachability (sends Authorization / API-key headers when auth is configured)
python -m swagger_gen --config config.yaml --ping-servers --clean-cache

# Authenticated GET of auth.probe_path (or --probe-path / --check-api default /)
export ORDERS_USER=…
export ORDERS_PASSWORD=…
python -m swagger_gen --config config.yaml --check-api --validate
```

`--ping-servers` sends HTTP `HEAD` (then `GET`) from **your machine** to each
**configured** host. `--check-api` always uses GET on `probe_path` and treats
`401`/`403` as failure when credentials were sent (wrong or expired token).
When `token_url` is set, an access token is fetched first (no redirects;
loopback/metadata IdPs are refused).
URLs that only appear inside an imported OpenAPI file are not probed.
Redirects are not followed, and cloud-metadata / loopback addresses are refused.

`--clean-cache` deletes `.swagger-gen-cache/` afterwards so private clones are
not left on a shared agent.

Ad-hoc:

```bash
python -m swagger_gen \
  --repo ./examples/sample-repos/fastapi-orders \
  --server https://orders.internal.example.com \
  --auth-type oauth2 --token-url https://orders.internal.example.com/token \
  --auth-grant password --username-env ORDERS_USER --password-env ORDERS_PASSWORD \
  --probe-path /health --check-api --validate
```

## 7. Remote git sources

```bash
python -m swagger_gen \
  --repo git@github.com:org/your-service.git \
  --ref v1.4.0 \
  --server https://your-service.example.com \
  --validate -v
```

Equivalent YAML:

```yaml
repos:
  - name: your-service
    url: https://github.com/org/your-service.git
    ref: v1.4.0
    host: https://your-service.example.com
    auth:
      type: bearer
      token_env: YOUR_SERVICE_TOKEN
      probe_path: /health
```

Clones land in `.swagger-gen-cache/` (gitignored) and are reused on the next
run. Private repos use your existing git credentials; the tool will not prompt
for a password.

## Troubleshooting

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| Try-it-out URL is `http://localhost:…` or the UI origin | No `host`/`servers`/`--server`, or the imported spec only had localhost and you did not override it | Set the deployed URL and regenerate. Confirm `host:` in the generator log |
| `Failed to fetch` / CORS error in the browser | The API does not allow origin `http://127.0.0.1:8765` | Call the same URL with curl, or host the HTML on a origin the API allows |
| `file://` page will not execute requests | Browser security around local files | Use `python -m http.server` as in step 2 |
| 401 / 403 from the real host | Auth not sent, token expired, or IdP credentials wrong | Set `auth` + export env vars; click **Authorize**; or `--check-api` after exporting. For `oauth2`, confirm `token_url` and grant. |
| 404 on a path that exists in the spec | Gateway prefix / `base_path` mismatch | Set `base_path` or put the prefix in `host` (e.g. `https://api.example.com/orders`) |
| Combined UI hits the wrong service | Two repos share a path | Use the per-service `.html` file |
| `git is required to clone…` | `git` not on `PATH` | Install git, or clone yourself and pass `--repo /local/path` |
| Clone fails for a private URL | No credentials in this environment | Use SSH (`git@…`) with an agent, or a local checkout |

## Quick checklist

- [ ] Every repo has `path` and/or `url`
- [ ] Every repo has `host` or `servers` pointing at the **deployed** API
- [ ] Protected services have `auth` (`bearer` / `basic` / `apikey` / `oauth2`) and env vars exported
- [ ] `python -m swagger_gen --config config.yaml --validate` exits 0
- [ ] `--check-api` returns 2xx (or a documented 404 on the probe path) with auth sent
- [ ] Each `*.openapi.yaml` lists that host under `servers:`
- [ ] Swagger UI dropdown shows the deployed URL
- [ ] Execute at least one read operation; Request URL uses that host
- [ ] If the browser is blocked, the same URL succeeds (or 401s) with curl
