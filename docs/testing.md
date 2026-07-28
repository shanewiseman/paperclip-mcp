# Testing

The native gate is:

```bash
scripts/ci-bootstrap.sh
scripts/validate.sh
```

It verifies:

- the frozen dependency graph;
- Ruff lint and formatting;
- strict mypy and Python compilation;
- configuration, credential separation, and `local_mode`;
- all 475 paths and 590 method/path operations mapping one-to-one to unique direct tools;
- OpenAPI nullable and exclusive-bound translation;
- path/query/body/multipart request serialization;
- credential attachment only for authenticated upstream operations;
- JSON, text, binary, errors, timeouts, and size limits;
- health/readiness and Streamable HTTP bearer behavior;
- generated documentation reproducibility;
- wheel/sdist contents and installed-package access to the OpenAPI resource;
- base and authenticated Compose contract rendering.

Mock transports prove gateway behavior, not live Paperclip compatibility. Live acceptance
should separately cover one public read, one authenticated or `local_mode` read, a
representative safe write, an expected authorization failure, and binary/download handling.
Use a disposable Paperclip company and do not fetch or print production secret values.

Building and scanning the final container, and testing against a live Paperclip version
matching the supplied OpenAPI, are release gates but may be unavailable in a source-only
development environment. Report them separately rather than inferring them from unit tests.
