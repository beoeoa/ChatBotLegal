# Roadmap: Hoan Thien He Thong Legal Assistant Hai Phong

## Muc tieu

Hoan thien toan bo he thong theo dung dinh huong legal assistant cho cap phuong/xa Hai Phong:

- tra loi dung van ban, khong bao gio tu suy dien thanh nguon phap ly
- phan loai theo role ro rang: `citizen`, `officer`, `admin`
- crawler, ingestion, retrieval va chat phai co nguon goc va co the kiem chung
- UI admin/phap ly sach UTF-8, nhat quan, de kiem thu va de van hanh

Ke hoach nay uu tien de hoan tat theo tung lop co the test duoc, khong lam lai toan bo he thong.

## Nguyen tac thuc thi

1. San pham phai co chu ky: spec -> build -> verify -> review -> ship.
2. Moi thay doi lien quan den legal grounding, ingestion, retrieval, background job, auth, hoac role phai co gate kiem chung.
3. Khong them dich vu ngoai can thiet, khong lam tang do phuc tap van hanh neu khong mang lai loi ich ro rang.
4. Chuan chi tiet la: khong bau ra dieu luat, khong bau ra hanh vi role, khong bau ra metadata.

## Pham vi 4 khoi chuc nang

### 1) Quan ly nguoi dung va du lieu nguoi dung

Muc tieu:

- chuan hoa vong doi nguoi dung, dang nhap, phan quyen, khoa/mo khoa
- dong bo trang thai role giua backend, frontend, session va auth middleware
- dam bao moi man admin chi hien va cho phep thao tac dung quyen

Hoan thanh khi:

- co cac route CRUD user ro rang va ket qua phan quyen dung
- admin khong the vo tinh truy cap chuc nang citizen/officer neu khong du quyen
- giao dien users hien thi dung trang thai, role, va loi xac thuc
- co test cho truong hop role, session, password va auth boundary

### 2) Crawler du lieu tu dong va ban tu dong

Muc tieu:

- hoan thien dong chay: thu thap -> loc -> review -> duyet -> nhap kho
- tach ro che do automatic va semi-automatic
- co trace cho admin biet vi sao mot tai lieu bi loai hoac duoc duyet

Hoan thanh khi:

- candidate queue co trang thai, ly do loai, va metadata
- import khong nhap nham van ban het hieu luc hoac thieu metadata quan trong
- fallback extractor hoat dong on dinh khi adapter nang hon khong san sang
- co test cho quy tac loc, priority, va fail-safe ingestion

### 3) Phan chia linh vuc va xu ly du lieu hanh chinh cong cap Xa/Phuong

Muc tieu:

- chuan hoa scope, domain, priority giua van ban trung uong va van ban dia phuong
- dam bao local document chi la context uu tien khi hop le, khong de an cao hon van ban trung uong
- dong bo retrieval, search filter va build scope theo cap hanh chinh

Hoan thanh khi:

- co bang mapping scope/domain ro rang va duoc ap dung nhat quan
- retrieval co the giai thich vi sao document duoc uu tien
- test duoc cac truong hop trung uong vs dia phuong, het hieu luc vs con hieu luc, dung cap vs sai cap
- bo golden set co the danh gia retriever va grounded answer theo domain

### 4) Giao dien chatbot va cau hinh he thong

Muc tieu:

- hoan thien login, dashboard, search/chat, settings va trang thai dich vu
- dọn sach mojibake/UTF-8 con sot tren man phap ly/admin
- lam ro local/cloud switch va tinh trang pilot

Hoan thanh khi:

- dashboard va chatbot hien thi nhat quan, khong loi text encoding
- nguoi dung biet ro he thong dang chay local hay cloud, model nao dang dung, fallback nao dang bat
- chat co nguon dan va cam xuat cau tra loi vuot qua evidence
- browser smoke test qua cac route chinh khong con loi render hay action

## Thu tu uu tien de lam tiep

### Phase A - Khoa chat va legal grounding

Ly do: day la lop co rui ro cao nhat. Neu answer sai legal grounding thi toan bo he thong mat gia tri.

Cong viec:

- ranh gioi chat theo nguon tin can cu va role
- chot golden set phap ly lam baseline bat buoc
- chay va giu ket qua validator/benchmark cho tap cau hoi chuan
- sua cac truong hop chat co the suy dien qua muc

Gate:

- `python scripts/validate_legal_golden_set.py` phai pass
- role-aware smoke test phai pass cho `citizen`, `officer`, `admin`
- ca cau tra loi phai co source grounding hoac fallback ro rang

### Phase B - Hoan thien user/admin/data management

Ly do: phan quyen dung la dieu kien truoc cho crawler, duyet data, va dashboard.

Cong viec:

- chot CRUD user, lock/unlock, reset credential, va session invalidation
- dong bo UI users voi backend auth state
- danh dau man admin-only va route guard

Gate:

- UI khong lo chuc nang sai role
- API tra loi dung ma loi auth/authorization
- co test cho role matrix

### Phase C - Hoan thien crawler va candidate review queue

Ly do: ingestion la dau vao cua retrieval, neu sai thi chat se sai theo.

Cong viec:

- hoan thien source trace, metadata validation, va candidate review
- phan biet auto vs semi-auto import
- them guard cho van ban het hieu luc, thieu thoi han, thieu cap ap dung

Gate:

- import khong the nhap nham tai lieu khong hop le
- candidate review co the quyet dinh duyet/tu choi co ly do
- co test cho locator, validation, va fallback parser

### Phase D - Hoan thien scope/field mapping cap Xa/Phuong

Ly do: day la logic dac thu cua domain Hai Phong, can on dinh truoc khi mo rong chat hoac crawl.

Cong viec:

- chuan hoa taxonomy scope/domain/priority
- dap ung dung cap hanh chinh va loai bo cross-domain noise
- dong bo voi search filters va evaluation scripts

Gate:

- query mau tra ve dung cap, dung field, dung hieu luc
- co report evaluation theo domain va role

### Phase E - Don UI, encoding va config surface

Ly do: hoan thanh tra nghiem de tri kha nang van hanh va demo.

Cong viec:

- quet va sua mojibake/UTF-8 trong man phap ly/admin
- chot local/cloud switch, status card, va service health display
- browser smoke test cho route chinh

Gate:

- khong con chuoi encoding loi trong man phap ly/admin quan trong
- frontend build va smoke test pass
- user co the nhin thay trang thai he thong dung

## Milestone cu the

### Milestone 1: Legal grounding baseline

Output:

- golden set hop le
- validator pass
- chat co fallback explicit khi evidence yeu

### Milestone 2: Role-safe auth and user management

Output:

- phan quyen dung cho 3 role
- man admin user management on dinh
- auth/session behavior co test

### Milestone 3: Trusted ingestion pipeline

Output:

- crawler candidate review queue hoan chinh
- metadata guard du
- extraction fallback on dinh

### Milestone 4: Scope-aware retrieval for ward/commune law

Output:

- scope mapping ro rang
- retrieval giam sai domain
- benchmark theo field

### Milestone 5: Production-ready UI

Output:

- dashboard sach encoding
- service status ro rang
- browser smoke pass

## De bai chat phai dat

He thong khong chi tra loi dung dieu luat, ma con phai:

- dung vai tro nguoi hoi
- dung muc quyen truy cap
- dung cap hanh chinh va pham vi tham quyen
- co can cu va fallback khi khong du du lieu

Vi du ky vong:

- `citizen`: tra loi de hieu, khong tra ve noi bo nhay cam, khong xuat qua quyen
- `officer`: co the tra loi quy trinh, ho so, tham quyen, va can cu van ban
- `admin`: co the xem trang thai ingestion, crawler, quality, va operational controls

## Verification gate bat buoc

- frontend production build pass
- backend health pass
- browser smoke test pass cho cac route chinh
- legal golden set validator pass
- role-based answer checks pass
- search/retrieval khong tra sai field hoac sai cap
- encoding scan khong con mojibake o man phap ly/admin quan trong
- citation co `link_status`, forms quality report va trace_id de truy nguyen loi runtime

## Definition of Done

Duoc coi la hoan thien khi:

1. Chat tra loi grounded, role-aware, va khong bau ra legal facts.
2. Admin/user/crawler/settings UI dung quyen va dung trang thai.
3. Ingestion va retrieval co trace, validator, va fallback ro rang.
4. UI khong con loi encoding dang chu y.
5. Tat ca gate verification quan trong deu pass va co the lap lai.

## Verification update - 2026-07-13

- Citation builder now matches explicit law/article mentions against retrieved
  metadata and emits `link_status` plus internal/source URLs.
- Ask errors include a stable `trace_id`; AskResponse exposes conversation,
  latency, trace and non-fatal error fields.
- Legal data audit now understands `source_page_url` and
  `source_download_url` in the official forms index.
- Golden set contains 167 cases, including 30 cases for each of the five
  canonical ward/commune domains.
- Focused persistence, citation, forms and legal-data tests pass. Full suite
  remains a separate integration gate because one long-running integration
  group requires an external service/runtime.
