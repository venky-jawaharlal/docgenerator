"""swagger_gen: static, multi-stack OpenAPI/Swagger documentation generator.

Given a config file that lists local directories and/or git repository URLs,
this package auto-detects the web framework(s) used in each repo, statically
analyzes the source code to extract API endpoints, parameters, request/response
bodies and authentication/session details, and emits OpenAPI 3.0 documents plus
a browsable Swagger UI. Configured deployment hosts are written into the spec
so Try-it-out targets the live service instead of localhost.
"""

__version__ = "0.2.0"
