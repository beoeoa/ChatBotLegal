# Development

Welcome to the Open Notebook development documentation! Whether you're contributing code, understanding our architecture, or maintaining the project, you'll find guidance here.

Recent operational note: [API cold-start dependency audit](api-cold-start-lazy-dependencies-2026-07-16.md).

## 🎯 Pick Your Path

### 👨‍💻 I Want to Contribute Code

Start with **[Contributing Guide](contributing.md)** for the workflow, then check:
- **[Quick Start](quick-start.md)** - Clone, install, verify in 5 minutes
- **[Development Setup](development-setup.md)** - Complete local environment guide
- **[Code Standards](code-standards.md)** - How to write code that fits our style
- **[Testing](testing.md)** - How to write and run tests

**First time?** Check out our [Contributing Guide](contributing.md) for the issue-first workflow.

### 🔒 I Want to Understand Security Practices

**[Security Guidelines](security.md)** covers:
- Database query safety (preventing SurrealQL injection)
- Template rendering safety (preventing SSTI)
- File handling safety (preventing path traversal and LFI)
- Secrets management and CORS configuration
- Code review security checklist

---

### 🏗️ I Want to Understand the Architecture

**[Architecture Overview](architecture.md)** covers:
- 3-tier system design
- Tech stack and rationale
- Key components and workflows
- Design patterns we use

For deeper dives, check `/open_notebook/` CLAUDE.md for component-specific guidance.

---

### 👨‍🔧 I'm a Maintainer

**[Maintainer Guide](maintainer-guide.md)** covers:
- Issue triage and management
- Pull request review process
- Communication templates
- Best practices

---

## 📚 Quick Links

| Document | For | Purpose |
|---|---|---|
| [Quick Start](quick-start.md) | New developers | Clone, install, and verify setup (5 min) |
| [Development Setup](development-setup.md) | Local development | Complete environment setup guide |
| [Contributing](contributing.md) | Code contributors | Workflow: issue → code → PR |
| [Code Standards](code-standards.md) | Writing code | Style guides for Python, FastAPI, DB |
| [Testing](testing.md) | Testing code | How to write and run tests |
| [Architecture](architecture.md) | Understanding system | System design, tech stack, workflows |
| [Admin Operations Dashboard](admin-operations-dashboard-20260811.md) | Admin UI maintainers | Read-only operations snapshot, alerts, role routing and safety boundaries |
| [Feature 018 Production Readiness](feature018-production-release-readiness.md) | Release and platform maintainers | Capability Registry, exact route boundaries, Go/No-Go evidence and rollout safety |
| [Design Principles](design-principles.md) | All developers | What guides our decisions |
| [API Reference](api-reference.md) | Building integrations | Complete REST API documentation |
| [Security](security.md) | All developers | Security practices and vulnerability prevention |
| [Retrieval Benchmark & Quality Gates](retrieval-benchmark-quality-gates.md) | Legal QA maintainers | Privacy-safe cold/warm, concurrency, Recall@6 and critical-authority benchmark |
| [Legal Answer Pipeline V2](legal-answer-pipeline-v2-20260811.md) | Legal QA maintainers | Deterministic routing, RRF retrieval, single generation/validator, rollout and rollback |
| [M2 Serving Manifest](m2-serving-manifest-2026-08-15.md) | Retrieval and release maintainers | Immutable serving truth, backend visibility, fail-closed retrieval/citation scope and acceptance evidence |
| [M3 Retrieval Baseline](m3-retrieval-baseline-2026-08-15.md) | Retrieval and legal QA maintainers | Candidate-3000 baseline, complete criterion-29 trace contract and immutable evidence |
| [M4 Query Normalization and Classification](m4-query-normalization-classification-2026-08-15.md) | Retrieval and legal QA maintainers | Structured 16-intent classifier and fail-closed current/historical/unknown temporal routing |
| [M5 Hybrid Retrieval](m5-hybrid-retrieval-2026-08-15.md) | Retrieval and legal QA maintainers | Manifest-bound vector/lexical Top-K and RRF/weighted fusion experiments on Golden 1,000 |
| [M6.1 Reranker Model Benchmark](m61-reranker-model-benchmark-2026-08-15.md) | Retrieval and legal QA maintainers | Pinned local BGE/GTE Golden-100 comparison, cold/warm CUDA latency, safety audit and non-activation decision |
| [Retrieval Quality V2](retrieval-quality-v2-2026-08-15.md) | Retrieval and legal QA maintainers | Corrected Recall@10/MRR@10 contract, temporal routing, source/article gaps, stage diagnostics and M6 fail-closed gates |
| [Retrieval Release V2 Inventory](retrieval-release-v2-inventory-2026-08-16.md) | Retrieval and legal QA maintainers | Read-only reconciliation of all 12,236 documents, current/history/quarantine partition and sealed 2,000-case acceptance suite |
| [Pilot Ask Quality Gate & Role Rollout](pilot-quality-gate-and-role-rollout.md) | Pilot operators | Offline 30-case/240-attempt gate, 24-hour role soak, credential safety and rollback |
| [Complete Roadmap](roadmap-complete-system.md) | All developers | End-to-end plan for legal assistant completion, role safety, ingestion, retrieval, and UI hardening |
| [Maintainer Guide](maintainer-guide.md) | Maintainers | Managing issues, PRs, releases |

---

## 🚀 Current Development Priorities

We're actively looking for help with:

1. **Frontend Enhancement** - Improve Next.js/React UI with real-time updates
2. **Performance** - Async processing and caching optimizations
3. **Testing** - Expand test coverage across components
4. **Documentation** - API examples and developer guides
5. **Integrations** - New content sources and AI providers

See GitHub Issues labeled `good first issue` or `help wanted`.

---

## 💬 Getting Help

- **Discord**: [Join our server](https://discord.gg/37XJPXfz2w) for real-time discussions
- **GitHub Discussions**: For architecture questions
- **GitHub Issues**: For bugs and features

Don't be shy! We're here to help new contributors succeed.

---

## 📖 Additional Resources

### External Documentation
- [FastAPI Docs](https://fastapi.tiangolo.com/)
- [SurrealDB Docs](https://surrealdb.com/docs)
- [LangChain Docs](https://python.langchain.com/)
- [Next.js Docs](https://nextjs.org/docs)

### Our Libraries
- [Esperanto](https://github.com/lfnovo/esperanto) - Multi-provider AI abstraction
- [Content Core](https://github.com/lfnovo/content-core) - Content processing
- [Podcast Creator](https://github.com/lfnovo/podcast-creator) - Podcast generation

---

Ready to get started? Head over to **[Quick Start](quick-start.md)**! 🎉

