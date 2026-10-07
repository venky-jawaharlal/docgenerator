# swagger_gen — multi-stack Swagger/OpenAPI documentation generator

Point it at **local directories and/or git repository URLs** (Java, Python,
Cloud Run services, etc.) and it will **statically analyze** each one,
auto-detect the web framework(s) in use, extract the API surface, and emit
**OpenAPI 3.0** documents plus a browsable **Swagger UI**.

Configure the **deployment host** and **authentication** for each service so
Swagger UI **Try it out** and `--check-api` send requests to the live API
(with a bearer token, basic credentials, an API key, or an OAuth2 access
token) instead of localhost.

Source is parsed, not executed. Cloning a git URL is the only network step
required at generation time.

## Supported tech stacks

| Stack | What is detected |
| --- | --- |
| **Spring Boot** (Java) | `@RestController`/`@RequestMapping`, `@Get/Post/Put/Delete/PatchMapping`, `@PathVariable`, `@RequestParam`, `@RequestHeader`, `@RequestBody`, `@PreAuthorize`/`@Secured` auth, `HttpSession` sessions |
| **JAX-RS** (Java) | `@Path`, `@GET/@POST/...`, `@PathParam`, `@QueryParam`, `@HeaderParam` |
| **FastAPI** (Python) | path operations, path/query params, Pydantic request bodies, `Depends`/`Security` + OAuth2/HTTP bearer auth, router prefixes |
| **Flask** (Python) | `@app.route` + method shortcuts, path/query params, JSON bodies, `session`/`login_required` auth |
| **Django** (Python) | `urls.py` routes (`path`/`re_path`/`url`) and view names |
| **Existing OpenAPI/Swagger** | Any `openapi.*` / `swagger.*` (`.yaml`/`.yml`/`.json`) file is imported as-is (highest priority). Localhost `servers` entries are dropped when you configure a real host. |

Auth/session details (`securitySchemes` such as JWT bearer or session cookies)
are inferred from the code and attached to the operations that require them.

## Install

```bash
pip install -r requirements.txt   # only dependency is PyYAML
```

Requires Python 3.9+. Remote git sources also need `git` on `PATH`.

## Usage

```bash
# 1. Copy and edit the example config to point at your repos and their hosts
cp config.example.yaml config.yaml

# 2. Generate docs
python -m swagger_gen --config config.yaml

# Local directories, git URLs, or a mix (no config file):
python -m swagger_gen \
  --repo /path/to/repoA \
  --repo https://github.com/org/repoB.git \
  --server https://api.example.com \
  -o ./out

# Pin a branch/tag/commit for git URLs:
python -m swagger_gen --repo https://github.com/org/repoB.git --ref main \
  --server https://api.example.com

# Check the spec and that Try-it-out will hit a deployed host:
python -m swagger_gen --config config.yaml --validate

# Live check against the deployed host (export tokens / IdP credentials first):
export ORDERS_USER=...
export ORDERS_PASSWORD=...
python -m swagger_gen --config config.yaml --validate --check-api

# Also HTTP-ping each configured host (sends auth headers when configured):
python -m swagger_gen --config config.yaml --validate --ping-servers

# Open Swagger UI via the CORS-safe local proxy (recommended after adding auth):
python -m swagger_gen --config config.yaml --serve
```

Open `swagger-output/index.html` in a browser. The portal groups endpoints by
sub-project in a sidebar. Repos start collapsed; expand one to see its
sub-projects. Drag the divider to resize the panes, or hide the list from the
top bar. Search, then open a service to try it. Follow
[`TESTING.md`](./TESTING.md) to exercise Try-it-out against the deployed host.

### CLI options

| Flag | Description |
| --- | --- |
| `-c, --config PATH` | YAML config file (default `config.yaml`) |
| `-o, --output DIR` | Override the output directory |
| `-f, --formats ...` | Override output formats (`yaml json html`) |
| `--repo PATH_OR_URL` | Local directory or git URL (repeatable); skips config repos |
| `--ref REF` | Branch, tag, or commit to check out for git `--repo` URLs |
| `--git-token-env` | Env var with an HTTPS git token used to clone private repos |
| `--git-username-env` / `--git-password-env` | Env vars for git HTTPS username and password |
| `--server URL` | Deployment host for Try-it-out (repeatable). With `--repo`, applied to every source; with `--config`, prepended to each repo's hosts |
| `--host URL` | Shorthand for a single `--server` |
| `--auth-type` | `bearer` / `jwt` / `basic` / `apikey` / `header` / `oauth2` for the live instance |
| `--token-env NAME` | Env var with a bearer token or API key (preferred over `--token`) |
| `--username-env` / `--password-env` | Env vars for HTTP Basic or OAuth2 password-grant credentials |
| `--api-key-env NAME` | Env var with an API key |
| `--auth-header NAME` | Header for `apikey` / `header` auth (default `X-API-Key`) |
| `--token-url URL` | OAuth2 token endpoint used to fetch an access token for live checks |
| `--auth-grant` | `client_credentials` or `password` (used with `--token-url`) |
| `--client-id-env` / `--client-secret-env` | Env vars for the OAuth2 client |
| `--scope` | Scope string sent to the token endpoint |
| `--probe-path PATH` | Path used by `--check-api` (default `/`) |
| `--check-api` | GET the probe path on each configured host using configured auth |
| `--embed-auth` | Pre-authorize generated HTML (do not publish those files) |
| `--serve` | Serve Swagger UI locally and proxy Try-it-out to configured hosts (avoids CORS) |
| `--port` / `--bind` | Port and address for `--serve` (default `8765` / `127.0.0.1`) |
| `--validate` | Fail if a document is malformed or has no non-localhost server |
| `--ping-servers` | HTTP HEAD/GET each **operator-configured** deployment host (not URLs imported from specs). Blocks redirects and cloud-metadata IPs |
| `--clean-cache` | Delete `.swagger-gen-cache/` after generation (recommended in CI) |
| `-v, --verbose` | Print every discovered endpoint + notes |

## Configuration

See [`config.example.yaml`](./config.example.yaml). Repos can be listed inline
under `repos:` and/or referenced from a plain-text file via `repos_file:`
(see [`repos.example.txt`](./repos.example.txt)).

```yaml
output:
  directory: ./swagger-output
  formats: [yaml, json, html]
  per_repo: true      # one document per repo
  combined: true      # also a single merged document

clone_cache: ./.swagger-gen-cache   # shallow clones of git URLs

# Optional fallback applied to repos that omit host/servers:
# default_host: https://api.gateway.example.com

repos:
  # Local checkout
  - name: user-service
    path: /abs/or/relative/path/to/repo
    host: https://users.example.com          # Try-it-out target (not localhost)
    auth:
      type: bearer
      token_env: USERS_BEARER_TOKEN         # never commit the token itself
      apply_to: all                         # send auth on every Try-it-out call
      probe_path: /health
      # OAuth2 alternative (fetches a token at --check-api time):
      # type: oauth2
      # grant: client_credentials
      # token_url: https://idp.example.com/oauth/token
      # client_id_env: USERS_CLIENT_ID
      # client_secret_env: USERS_CLIENT_SECRET

  # Remote git (cloned on demand). `ref` is optional.
  # Put the clone token in the environment, not in this file.
  - name: orders-service
    url: https://github.com/org/orders-service.git
    ref: main
    git:
      token: ghp_…                 # paste the PAT here; config.yaml is gitignored
      # token_env: GH_TOKEN        # alternative: name of an env var, not the token
      # username: oauth2           # GitLab. GitHub uses x-access-token by default
    servers:
      - url: https://orders.example.com
        description: Production
      - url: https://orders.staging.example.com
        description: Staging

  # Both: use the local path when it exists, otherwise clone `url`
  - name: catalog
    path: ~/src/product-catalog
    url: git@github.com:org/product-catalog.git
    host: https://catalog.example.com
```

`host` is a shorthand for a single server URL. `servers` accepts strings or
`{url, description}` objects. Non-localhost URLs are listed first so Swagger UI
defaults to the deployed service.

`auth` declares how the **deployed** instance authenticates. Supported types:

| `type` | Credentials | Sent as |
| --- | --- | --- |
| `bearer` / `jwt` | `token_env` or `token` (`${VAR}` expanded). Optional `token_url` to fetch a token | `Authorization: Bearer …` |
| `basic` | `username_env` + `password_env` | HTTP Basic |
| `apikey` | `api_key_env` | Header (`X-API-Key` or `header:`) |
| `header` | `token_env` + `header:` | Custom header |
| `oauth2` | `token_url` + `client_id_env`/`client_secret_env` (`grant: client_credentials`) or `username_env`/`password_env` (`grant: password`) | Fetches `access_token`, then `Authorization: Bearer …` |

`apply_to: all` (default) attaches the scheme to every operation so Try-it-out
sends credentials after you click **Authorize** (or after `--embed-auth`).
`apply_to: detected` attaches it only to operations the analyzer already marked
as secured. `apply_to: none` documents the scheme without attaching it.

Secrets are resolved from the environment at generate/check time and are **not**
written into OpenAPI YAML/JSON. Inline values may use `${VAR}`; a bare `$VAR`
is expanded only when that variable is set, so a password like `p$ssword` is
left intact.

If an imported OpenAPI file only declares `http://localhost:…`, those entries
are dropped and replaced by the configured host.

## How it works

```
config.yaml ─▶ resolve (local path or git clone) ─▶ scanner ─▶ analyzers
    ─▶ ApiSpec (IR) ─▶ OpenAPI builder (injects servers) ─▶ yaml/json/html
```

1. **resolve** uses a local `path` when it is a directory; otherwise it
   shallow-clones `url` (or a git `--repo` argument) into `clone_cache`.
2. **scanner** walks the tree once (pruning `node_modules`, `target`, `.git`, …)
   and runs every analyzer whose `detect()` matches.
3. Each **analyzer** emits a framework-agnostic intermediate representation
   (`ApiSpec` / `Endpoint` / `Parameter` / `SecurityScheme`).
4. The **builder** turns that into a valid OpenAPI 3.0.3 document with
   `servers` set to the deployment host; the **output** layer writes YAML/JSON
   and a self-contained Swagger UI HTML page.

## Extending to a new stack

Add a `BaseAnalyzer` subclass in `swagger_gen/analyzers/`, implement `detect()`
and `analyze()`, and register it in `swagger_gen/analyzers/__init__.py`. The rest
of the pipeline is unchanged.

## Limitations

Static analysis is heuristic. Dynamically registered routes, heavy
metaprogramming, and non-annotated request/response bodies may be missed or only
partially typed. Where a repo already ships a hand-written OpenAPI spec, that
file is imported verbatim and takes priority (except localhost `servers`, which
are replaced by your configured host). Django routes have no HTTP verb in
the URLconf, so they are documented as `GET` with a note.

Private HTTPS git URLs can take the token directly as `git.token` in
`config.yaml` (that file is gitignored), or a username and password as
`git.username` / `git.password`. `git.token_env` is only the **name** of an
environment variable, not the token. The same applies to `--git-token-env`.
The secret is passed to git through a private askpass helper and is not stored
in the clone URL or the generated docs. SSH URLs (`git@…`) still use your SSH
agent. The generator never prompts for a password. Tokens embedded in clone URLs
are stripped before clone and redacted from logs.

Combined documents attach each service's host at the path level and tag
operations by sub-project. If two services share the same path, prefer the
portal or the per-service HTML for Try-it-out.

## Security notes

- **Static analysis only** — cloned and local source is parsed, never executed.
  Git hooks are disabled and checkouts are created with `core.symlinks=false`.
- **Deployment hosts** — OpenAPI `servers` accept `http`/`https` only. `file://`,
  `javascript:`, and similar schemes are dropped. Localhost imported from a spec
  is replaced by your configured host.
- **`--serve`** binds to loopback by default and proxies Try-it-out only to
  hosts you configured. It does not follow redirects and refuses
  loopback/cloud-metadata targets. Do not `--bind 0.0.0.0` on a shared network.
- **`--ping-servers`** probes only hosts you set in config/CLI, does not follow
  redirects, and refuses loopback/cloud-metadata targets. Do not point it at
  untrusted URLs.
- **Auth secrets** stay in environment variables. OpenAPI YAML/JSON never
  contain tokens or client secrets. `--embed-auth` / `auth.embed: true` writes
  them only into local HTML — do not publish that output. OAuth2 `token_url`
  fetches do not follow redirects and refuse loopback/cloud-metadata hosts.
- **Generated HTML** escapes repo-derived strings, does not persist Try-it-out
  credentials in `localStorage`, and does not submit the spec to Swagger's public
  validator.
- **Clone cache** is mode `0700` and gitignored. Use `--clean-cache` on shared CI
  agents so private trees are not left behind.

## Testing the generated docs

See [`TESTING.md`](./TESTING.md) for serving the UI with `--serve` (CORS proxy),
using Try-it-out against the deployed host, curl examples, and troubleshooting.
