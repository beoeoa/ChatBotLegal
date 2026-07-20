# Live Support Realtime ? B??c 10

## T?ng quan
K?nh h? tr? tr?c tuy?n gi?a ng??i d?n v? c?n b?, ??nh tuy?n theo l?nh v?c (domain), giao ti?p realtime qua WebSocket v?i fallback polling.

## Ki?n tr?c
- **File-backed store**: `data/support_tickets/` (ticket JSON), `data/support_attachments/` (file nh? ph?n).
- **WebSocket Hub**: `_Hub` trong-process qu?n l? session channels (`ticket_id`) v? queue watchers (`domain`).
- **Authentication**: Token-based khi c? real users; legacy mode x?c th?c qua query params + role/password mapping.

## State machine
```
waiting ? assigned ? active ? closed
```
- `waiting`: Citizen t?o ticket, h?ng ch? domain.
- `assigned`: Officer claim.
- `active`: C? tin nh?n trao ??i.
- `closed`: ??ng b?i citizen/officer/admin.

## Quy?n ri?ng t?
- Queue preview: ?n `citizen_id` v? n?i dung c?u h?i ? `"Y?u c?u h? tr? ?ang ch? ti?p nh?n."`.
- Officer ch? th?y n?i dung chat sau khi claim th?nh c?ng.
- Admin ph?i nh?p l? do nghi?p v? (`reason`) m?i xem ???c chi ti?t; thao t?c ???c ghi audit log.

## Audit log
- `support.chat.view`, `support.chat.messages.view`, `support.ticket.reassign`, `support.chat.websocket.view`.
- Khi audit backend kh?ng kh? d?ng ? tr? HTTP 503, kh?ng fail-open.

## Retention
- Tin nh?n/chat: l?u **12 th?ng**.
- T?p ??nh k?m: x?a **6 th?ng** sau khi ticket ??ng.
- Audit log: l?u **24 th?ng**.
- *L?u ?*: C? ch? purge t? ??ng ch?a ???c tri?n khai trong router hi?n t?i; c?n cron job ho?c background worker ?? d?n d?p theo `expires_at`.

## API Endpoints
| Method | Path | M? t? |
|--------|------|-------|
| POST | `/api/support/tickets` | Citizen t?o y?u c?u |
| GET | `/api/support/tickets` | Danh s?ch (citizen/officer/admin) |
| GET | `/api/support/queue` | H?ng ch? domain (officer/admin) |
| GET | `/api/support/my-domains` | L?nh v?c ???c ph?p c?a officer |
| GET | `/api/support/tickets/:id` | Chi ti?t ticket |
| GET | `/api/support/tickets/:id/messages` | L?ch s? tin nh?n |
| POST | `/api/support/tickets/:id/claim` | Officer nh?n phi?n |
| POST | `/api/support/tickets/:id/decline` | T? ch?i/chuy?n domain |
| PATCH | `/api/support/tickets/:id/assign` | Admin ph?n c?ng/reassignment |
| POST | `/api/support/tickets/:id/messages` | G?i tin nh?n |
| POST | `/api/support/tickets/:id/attachments` | Upload t?p ??nh k?m |
| GET | `/api/support/tickets/:id/attachments/:aid/download` | T?i t?p |
| PATCH | `/api/support/tickets/:id/close` | ??ng phi?n |
| POST | `/api/support/tickets/:id/rating` | ??nh gi? citizen |
| WS | `/api/support/ws` | WebSocket realtime |

## Domain Routing
- `ho_tich_chung_thuc`: H? t?ch - ch?ng th?c
- `dat_dai_xay_dung`: ??t ?ai - X?y d?ng
- `an_sinh_y_te_giao_duc`: An sinh - Y t? - Gi?o d?c
- `hanh_chinh_cong`: H?nh ch?nh c?ng
- `trat_tu_do_thi`: Tr?t t? ?? th?
- `cu_tru_an_ninh`: C? tr? - An ninh
- `khieu_nai_to_cao_xu_phat`: Khi?u n?i - X? ph?t

## Frontend
- `/live-support`: Giao di?n ch?nh cho citizen (t?o ticket) v? officer/admin (queue/chat).
- Escalation t? `/search`: G?i `domain` thay v? `department`; b?t bu?c ch?n l?nh v?c tr??c khi chuy?n.
- Queue notification: WebSocket watcher theo t?ng domain ? toast th?ng b?o khi c? ticket m?i.
- Attachment download: Fetch blob v?i Bearer token ? m? tab m?i, kh?ng crash n?u l?i.

## Tests
- `tests/test_live_support_integration.py`: 5 tests (routing, claim, transfer, attachment, admin audit, queue privacy, fail-closed audit).
- T?t c? pass.

## Migration & Rollback
- Kh?ng thay ??i schema SurrealDB c?.
- File store ??c l?p, c? th? rollback b?ng c?ch t?t router prefix `/support`.
- Backup `data/support_tickets/` v? `data/support_attachments/` tr??c khi deploy.
