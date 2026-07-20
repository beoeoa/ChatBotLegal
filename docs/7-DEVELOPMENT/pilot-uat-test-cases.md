# Pilot UAT - Phuong Le Chan

Bo test dung du lieu gia, khong dung CCCD, so dien thoai, dia chi hoac ho so that.
Khong bat `auto_import`/`auto_approve`. Moi FAIL ghi vao
`notebook_data/uat/uat-execution-report.json` voi `id`, `role`, `repro_steps`,
`expected`, `actual`, `severity`, `evidence`, `root_cause`, `owner`, `retest_status`.

Du lieu test: citizen `citizen_uat`; officers `officer_hotich`, `officer_diadat`,
`officer_ansinh`, `officer_hanhchinh`, `officer_trattu`; admin `admin_uat`.
Dia ban gia lap: phuong Le Chan, Hai Phong; nhan vat Nguyen Van A; thua dat `LC-UAT-001`.
File hop le: PDF text, DOCX, TXT. File loi: EXE, MP3, PDF scan, file >10 MB.
Candidate gia lap: 2 van ban va 1 bieu mau trang thai `candidate_pending_review`.

`PASS` chi khi co evidence URL/API/screenshot. Thieu session trinh duyet hoac thieu
nguoi cham khong duoc coi la PASS.

## Bước 21 - Chuẩn bị

| ID | Role | Preconditions | Steps | Expected | Evidence |
|---|---|---|---|---|---|
| UAT-21-01 | admin | Pilot bat | Kiem tra flags | 5 domain, chat, FAQ, support, viewer bat; auto import/approve tat | JSON flags |
| UAT-21-02 | admin | Candidate gia | Mo danh sach | 2-3 pending, co URL/hash/domain | URL + anh |
| UAT-21-03 | admin | Fixture | Kiem tra file | Fixture dung dinh dang, khong PII | hash |

## Bước 22 - Citizen

| ID | Role | Preconditions | Steps | Expected | Evidence |
|---|---|---|---|---|---|
| UAT-22-01 | citizen | Account test | Dang nhap/profile | Thanh cong, khong lo credential | URL |
| UAT-22-02 | citizen | Chat da tao | Hoi tiep cau truoc | Giu dung ngu canh | conversation id |
| UAT-22-03 | citizen | Citizen | Hoi khai sinh | De hieu, co buoc va nguon | response |
| UAT-22-04 | citizen | Citizen | Hoi dat viet tay | Co dieu kien, khong bia dieu/khoan | response |
| UAT-22-05 | citizen | Citizen | Hoi ngoai 5 domain | Noi ro pham vi, de xuat chuyen | response |
| UAT-22-06 | citizen | Co citation | Bam citation | Viewer dung dieu/khoan, highlight dung | URL + anh |
| UAT-22-07 | citizen | Co PDF | Tai PDF | Mo duoc, tieng Viet dung dau | hash |
| UAT-22-08 | citizen | Cau hoi form | Hoi form ket hon | Toi da 1-3 form phu hop, khong form gia official | response |
| UAT-22-09 | citizen | Co form | Tai form | File mo duoc, metadata dung | hash |
| UAT-22-10 | citizen | PDF/DOCX/TXT | Upload hop le | Tiep nhan dung trang thai | API |
| UAT-22-11 | citizen | EXE/MP3/scan/>10MB | Upload loi | Chan, thong bao ly do, khong luu file | API |
| UAT-22-12 | citizen | Citizen | Tao support ticket, chon domain | Dung queue; chi thay ticket cua minh | ticket id |
| UAT-22-13 | citizen | Citizen | Mo FAQ/domain suggestion | Hien dung FAQ va goi y | anh |
| UAT-22-14 | citizen | Citizen | Mo admin/candidate/ho so | 401/403, khong hien menu | status |

## Bước 23 - Năm cán bộ

Ap dung UAT-23-01 den UAT-23-10 cho ca 5 account; ghi ro domain va username.

| ID | Role | Preconditions | Steps | Expected | Evidence |
|---|---|---|---|---|---|
| UAT-23-01 | officer_hotich | Dang nhap | Hoi khai sinh/ket hon/chung thuc | Dung domain, citation | response |
| UAT-23-02 | officer_diadat | Dang nhap | Hoi sang ten dat/GPXD | Dung domain, khong suy dien | response |
| UAT-23-03 | officer_ansinh | Dang nhap | Hoi tro cap/y te/giao duc | Dung domain, quy trinh ro | response |
| UAT-23-04 | officer_hanhchinh | Dang nhap | Hoi cu tru/can cuoc/DVC | Dung domain, citation | response |
| UAT-23-05 | officer_trattu | Dang nhap | Hoi via he/do xe/xu phat | Dung tham quyen | response |
| UAT-23-06 | officer_* | Da dang nhap | Hoi sai domain | Hard-block, de xuat chuyen | response |
| UAT-23-07 | officer_* | Co queue | Xem/claim/chat/transfer/close | Chi dung queue; close co note | audit |
| UAT-23-08 | officer_* | Co candidate | Xem va gui proposal | Chi dung domain; khong import | API/audit |
| UAT-23-09 | officer_* | Dang nhap | Goi crawl/audit/users/models | 401/403 | status |
| UAT-23-10 | officer_* | Co du lieu domain khac | Thu IDOR case/ticket/candidate | 403/khong thay | status |

## Bước 24 - Admin

| ID | Role | Preconditions | Steps | Expected | Evidence |
|---|---|---|---|---|---|
| UAT-24-01 | admin | Dang nhap | Mo quality dashboard | Metrics hien, khong loi nghiem trong | URL/API |
| UAT-24-02 | admin | Pending metadata | Duyet metadata-only | Van pending, khong auto import | audit |
| UAT-24-03 | admin | PDF scan | Chay OCR | Co ket qua/reason, khong auto approve | job id |
| UAT-24-04 | admin | Candidate loi | Reject | Co ly do va audit | audit |
| UAT-24-05 | admin | Candidate hop le | Approve -> import -> retrieval | Chi import sau approve, co source | job/response |
| UAT-24-06 | admin | Form catalog | Xem/tai form | Khong form gia; mo duoc | URL/hash |
| UAT-24-07 | admin | Ticket | Phan cong/chuyen 5 domain | Dung domain, co audit | ticket audit |
| UAT-24-08 | admin | Sensitive detail | Mo thieu/co reason | Thieu bi chan; co reason audit | status |
| UAT-24-09 | admin | Retention | Dry-run | Chi bao cao, khong xoa | JSON |
| UAT-24-10 | admin | Source robots/loi | Scan | Ton trong robots/rate limit, co failure_reason | run id |
| UAT-24-11 | admin | Import fixture | Rollback | Chi rollback document test | job/audit |

## Bước 25 - Realtime

| ID | Role | Preconditions | Steps | Expected | Evidence |
|---|---|---|---|---|---|
| UAT-25-01 | citizen+officer | Hai session doc lap | Tao/claim/chat 5-10 tin | Realtime, khong duplicate/nham | timestamps |
| UAT-25-02 | citizen+officer | Ticket active | Gui attachment hop le | Dung ticket, unread dung | attachment id |
| UAT-25-03 | citizen+officer | Ticket active | Transfer/close/rating | Dung queue, close note, rating luu | audit |

## Bước 26 - Load smoke

| ID | Role | Preconditions | Steps | Expected | Evidence |
|---|---|---|---|---|---|
| UAT-26-01 | 10 citizen | Service healthy | Hoi 10 cau dong thoi | Khong timeout/500; p50/p95 | load report |
| UAT-26-02 | 5 officer+admin | Service healthy | Mo queue/dashboard dong thoi | Khong 403 sai/treo | metrics |
| UAT-26-03 | mixed | Service healthy | PDF/form/search/upload/support cung luc | Error/latency trong nguong | logs |

## Bước 27 - Security

| ID | Role | Preconditions | Steps | Expected | Evidence |
|---|---|---|---|---|---|
| UAT-27-01 | citizen | Session | Goi admin endpoint | 401/403 | status |
| UAT-27-02 | officer A | Co data B | Thu IDOR conversation/ticket/candidate | 403/khong thay | status |
| UAT-27-03 | citizen | Co file/PDF | Tai file khong duoc phep | Bi chan | status |
| UAT-27-04 | admin | Sensitive record | Thieu/co reason | Chan/mo dung, audit | audit |
| UAT-27-05 | all | Logs/bundle/source | Tim password/bootstrap | Khong lo, khong tracked | rg output |

## Bước 28 - Human quality

| ID | Role | Preconditions | Steps | Expected | Evidence |
|---|---|---|---|---|---|
| UAT-28-01 | citizen | 10 cau gold | Cham dung/de hieu/hanh dong/citation/form/an toan/toc do 1-5 | Dat nguong; diem <3 co RCA/retest | score |
| UAT-28-02 | officer | 10 cau gold | Cham nghiep vu | Dung dieu/khoan/tham quyen | score |
| UAT-28-03 | admin | 10 cau gold | Cham grounding/workflow | Nguon va workflow dung | score |

## Bước 29 - Go/No-Go

| ID | Role | Preconditions | Steps | Expected | Evidence |
|---|---|---|---|---|---|
| UAT-29-01 | admin | Co reports | Tong hop P0/P1, smoke, security, quality | GO chi khi khong P0/P1, smoke 100%, credential da doi/xoa, diem >=4/5 | go-no-go |
| UAT-29-02 | admin | Con issue | Kiem tra owner/deadline | Con gap thi NO-GO | decision |
