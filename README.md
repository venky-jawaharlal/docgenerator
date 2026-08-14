# swagger_gen — multi-stack Swagger/OpenAPI documentation generator

Point it at a list of local repositories (Java, Python, Cloud Run services,
etc.) and it will **statically analyze** each one, auto-detect the web
framework(s) in use, extract the API surface, and emit **OpenAPI 3.0** documents
plus a browsable **Swagger UI**.

No code is executed and no network/LLM is required — everything is derived by
parsing source files.

## Supported tech stacks

| Stack | What is detected |
| --- | --- |
| **Spring Boot** (Java) | `@RestController`/`@RequestMapping`, `@Get/Post/Put/Delete/PatchMapping`, `@PathVariable`, `@RequestParam`, `@RequestHeader`, `@RequestBody`, `@PreAuthorize`/`@Secured` auth, `HttpSession` sessions |
| **JAX-RS** (Java) | `@Path`, `@GET/@POST/...`, `@PathParam`, `@QueryParam`, `@HeaderParam` |
| **FastAPI** (Python) | path operations, path/query params, Pydantic request bodies, `Depends`/`Security` + OAuth2/HTTP bearer auth, router prefixes |
| **Flask** (Python) | `@app.route` + method shortcuts, path/query params, JSON bodies, `session`/`login_required` auth |
| **Django** (Python) | `urls.py` routes (`path`/`re_path`/`url`) and view names |
| **Existing OpenAPI/Swagger** | Any `openapi.*` / `swagger.*` (`.yaml`/`.yml`/`.json`) file is imported as-is (highest priority) |

Auth/session details (`securitySchemes` such as JWT bearer or session cookies)
are inferred from the code and attached to the operations that require them.

## Install

```bash
pip install -r requirements.txt   # only dependency is PyYAML
```

Requires Python 3.9+.

## Usage

```bash
# 1. Copy and edit the example config to point at your repos
cp config.example.yaml config.yaml

# 2. Generate docs
python -m swagger_gen --config config.yaml

# Quick one-off (no config file):
python -m swagger_gen --repo /path/to/repoA --repo /path/to/repoB -o ./out

# See exactly which endpoints/auth were found:
python -m swagger_gen --config config.yaml --verbose
```

Open `swagger-output/index.html` in a browser to explore all services, or open
an individual `<service>.html` for a single Swagger UI.

### CLI options

| Flag | Description |
| --- | --- |
| `-c, --config PATH` | YAML config file (default `config.yaml`) |
| `-o, --output DIR` | Override the output directory |
| `-f, --formats ...` | Override output formats (`yaml json html`) |
| `--repo PATH` | Analyze an ad-hoc repo (repeatable); ignores config repos |
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

repos:
  - name: user-service
    path: /abs/or/relative/path/to/repo
    servers: [https://users.example.com]   # optional
    # version: "1.2.0"                       # optional metadata override
    # frameworks: [spring]                   # optional: skip auto-detection
    # exclude: [legacy]                       # optional: extra dirs to ignore
```

## How it works

```
config.yaml ─▶ scanner ─▶ [analyzers per repo] ─▶ ApiSpec (IR) ─▶ OpenAPI builder ─▶ yaml/json/html
```

1. **scanner** walks each repo once (pruning `node_modules`, `target`, `.git`, …)
   and runs every analyzer whose `detect()` matches.
2. Each **analyzer** emits a framework-agnostic intermediate representation
   (`ApiSpec` / `Endpoint` / `Parameter` / `SecurityScheme`).
3. The **builder** turns that into a valid OpenAPI 3.0.3 document; the **output**
   layer writes YAML/JSON and a self-contained Swagger UI HTML page.

## Extending to a new stack

Add a `BaseAnalyzer` subclass in `swagger_gen/analyzers/`, implement `detect()`
and `analyze()`, and register it in `swagger_gen/analyzers/__init__.py`. The rest
of the pipeline is unchanged.

## Limitations

Static analysis is heuristic. Dynamically registered routes, heavy
metaprogramming, and non-annotated request/response bodies may be missed or only
partially typed. Where a repo already ships a hand-written OpenAPI spec, that
file is imported verbatim and takes priority. Django routes have no HTTP verb in
the URLconf, so they are documented as `GET` with a note.
