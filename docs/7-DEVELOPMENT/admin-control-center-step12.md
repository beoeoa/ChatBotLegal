# Admin Control Center ? B??c 12

## Route v? quy?n
- UI: `/admin-control`.
- API: `/api/admin/control/*`.
- To?n b? endpoint Control Center y?u c?u role `admin`; officer/citizen nh?n HTTP 403.

## Module
1. **User & Role**: danh s?ch t?i kho?n, role, l?nh v?c, ward scope, tr?ng th?i ho?t ??ng, first-password-change v? online state. Kh?a/m? kh?a/reset/g?n role-domain d?ng API User hi?n c?, b?t bu?c header `X-Business-Reason` v?i action nh?y c?m.
2. **Live Support**: queue, tr?ng th?i, tu?i phi?n, c?nh b?o SLA qu? 60 ph?t, l?ch s?/detail qua audit reason; reassignment/escalation duy tr? ? endpoint support hi?n c? v?i reason b?t bu?c.
3. **Legal Cases**: ch? metadata cho danh s?ch; m? n?i dung `/admin/control/legal-cases/{id}?reason=...` b?t bu?c l? do v? audit.
4. **Knowledge**: legal candidates, FAQ, forms inventory, OCR failure; approve/reject candidate b?t bu?c reason/audit. Documents/embeddings d?ng endpoint qu?n tr? hi?n c?, kh?ng duplicate ingestion pipeline.
5. **Quality**: insufficient evidence, citation URL ch?t/legacy VBPL, form thi?u/broken, OCR failure, feedback support, legal quality result.
6. **Audit**: read-only; filter actor role/resource type/resource id/action/date/reason. Kh?ng c? POST/PUT/PATCH/DELETE audit route.

## Audit invariant
C?c action nh?y c?m ph?i ghi `user_audit_log` tr??c khi tr? detail ho?c th?c hi?n:
- admin view support/case detail;
- admin download support attachment;
- user role/status/password reset;
- review candidate;
- support reassignment/escalation.

`write_audit_log` fail -> API fail closed (503) v?i c?c detail/action c?a Control Center.

## Online state
`live_support._Hub` theo d?i socket ?ang m? theo `user_id`. UI ch? hi?n th? presence t?m th?i; kh?ng l?u l?ch s? online state ngo?i audit/login hi?n c?.

## Tests
```powershell
pytest -q tests/test_admin_control_center.py tests/test_officer_seed_and_password.py tests/test_live_support_integration.py
cd frontend; npm run build
```

Tests cover reason-required, detail audit, audit filtering/read-only invariant, permission boundaries, SLA counters, support realtime ownership, and officer first-login controls.
