# Form discovery from chat

When a user explicitly asks for a form and no approved downloadable form is
available, the answer path returns `forms_unavailable` and does not invent a
download link. It queues `scripts/discover_missing_faq_forms.py` in a worker.

The worker searches official-domain pages and stores only
`candidate_pending_review` records. An admin must inspect the source, verify
the current procedure and approve the candidate before it can appear in FAQ or
chat recommendations. A slow or blocked website therefore cannot delay the
legal answer or turn an unverified file into a public form.

Admin endpoints:

- `POST /api/faq/discover-missing-forms?limit=50`
- `GET /api/faq/discover-missing-forms/status`

The same rule applies to forms shown by chat: only approved records with an
existing file or verified official download URL are returned.
