"""swagger_gen: static, multi-stack OpenAPI/Swagger documentation generator.

Given a config file that lists local repositories (Java, Python, etc.), this
package auto-detects the web framework(s) used in each repo, statically
analyzes the source code to extract API endpoints, parameters, request/response
bodies and authentication/session details, and emits OpenAPI 3.0 documents plus
a browsable Swagger UI.
"""

__version__ = "0.1.0"
