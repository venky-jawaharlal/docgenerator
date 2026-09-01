# Testing generated Swagger / OpenAPI documents

This guide is for documents produced by `swagger_gen`. The important rule:

> **Try-it-out and any live check must hit the deployed service (`servers` /
> `host` in config), not localhost.** The generator writes that host into the
> spec and will not invent a loopback default.

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

## 3. Execute an operation (Try it out)

1. Open the per-service HTML (preferred over `combined.html` when paths overlap).
2. Authorize if the operations use bearer/session security:
   - Click **Authorize**.
   - For JWT / OAuth2 bearer: paste the token (`bearerAuth` expects the raw
     token; Swagger UI adds the `Authorization` header).
   - Credentials are **not** stored in `localStorage` (re-authorize if you
     reload). Do not paste production secrets on a shared workstation.
   - For session-cookie APIs: Try-it-out from the browser only works if you
     are already logged in on that host (SameSite / CORS apply). Use curl if
     the UI cannot attach the cookie.
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

## 6. Ping the deployment host from the generator

```bash
python -m swagger_gen --config config.yaml --ping-servers --clean-cache
```

This sends HTTP `HEAD` (then `GET`) from **your machine** to each **configured**
host (`host` / `servers` / `--server`). URLs that only appear inside an imported
OpenAPI file are not probed. Redirects are not followed, and cloud-metadata /
loopback addresses are refused. A `2xx`/`3xx`/`4xx` means the host is reachable;
a timeout or DNS error means Try-it-out cannot work until the URL is fixed. It
does not log in or assert every route.

`--clean-cache` deletes `.swagger-gen-cache/` afterwards so private clones are
not left on a shared agent.

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
| 401 / 403 from the real host | Auth scheme is documented but you have no token/cookie | Use **Authorize** or pass `-H Authorization: Bearer …` |
| 404 on a path that exists in the spec | Gateway prefix / `base_path` mismatch | Set `base_path` or put the prefix in `host` (e.g. `https://api.example.com/orders`) |
| Combined UI hits the wrong service | Two repos share a path | Use the per-service `.html` file |
| `git is required to clone…` | `git` not on `PATH` | Install git, or clone yourself and pass `--repo /local/path` |
| Clone fails for a private URL | No credentials in this environment | Use SSH (`git@…`) with an agent, or a local checkout |

## Quick checklist

- [ ] Every repo has `path` and/or `url`
- [ ] Every repo has `host` or `servers` pointing at the **deployed** API
- [ ] `python -m swagger_gen --config config.yaml --validate` exits 0
- [ ] Each `*.openapi.yaml` lists that host under `servers:`
- [ ] Swagger UI dropdown shows the deployed URL
- [ ] Execute at least one read operation; Request URL uses that host
- [ ] If the browser is blocked, the same URL succeeds (or 401s) with curl
