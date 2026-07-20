# API cold-start dependency audit

Date: 2026-07-16

## Finding

A clean `import api.main` on the Windows target took roughly 28-36 seconds in
repeated offline diagnostics. A 20-second launch probe therefore looked like a
hang even though the import eventually completed. A timed stack dump showed
the process importing `transformers`, Torch and Sentence Transformers through
utility, Esperanto and model-provisioning imports before Uvicorn could bind its
port.

No existing process or listener was stopped during this audit.

## Safe reduction

- `open_notebook.utils` now resolves its legacy public exports lazily.
- text splitters are imported only when long text is actually chunked.
- Esperanto model factories and runtime model classes are imported only when
  inference/model discovery requests them, not merely when the model registry
  or readiness code is imported.
- model discovery classification is imported only by the explicit Ollama
  discovery workflow.

These boundaries preserve existing imports while keeping authentication and
readiness from initializing optional NLP runtimes by themselves. Subprocess
tests assert the lightweight imports do not load Torch, Transformers or text
splitters, and existing chunking/embedding/model/readiness tests cover runtime
compatibility.

## Remaining runtime evidence

The complete `api.main` module still imports all routers and podcast/command
registrations eagerly. On this machine another route-registration path still
loads Torch/Transformers, so cold startup remains hardware and environment
dependent and is not yet a release performance pass. Startup probes must allow
more than the measured cold-import window until router/command registration is
split into smaller lazy modules.

This audit does not enable a feature flag, start a provider, download a model,
change a legal record, or modify the legal corpus.

## Rollback

The lazy imports are internal compatibility boundaries and require no data
migration. They can be reverted independently; no persisted model, Ask or
corpus record changes shape.
