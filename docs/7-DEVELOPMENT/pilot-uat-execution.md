# Pilot UAT execution

`scripts/build_uat_execution_report.py` tao report tong hop cho Buoc 21-29 tai
`notebook_data/uat/uat-execution-report.json`.

## Cach doc ket qua

- `PASS`: co evidence tu test tu dong hoac runtime.
- `PARTIAL`: mot phan da co evidence, mot phan can thao tac UAT that.
- `NOT_RUN`: chua duoc xac nhan, khong coi la pass.
- `NO-GO`: chua du dieu kien mo rong pilot.

## Dieu kien moi truong local

Windows local runner phai co mot thu muc tam co quyen ghi. Khi chay crawler/OCR,
dat `TEMP` va `TMP` den `.tmp/python-temp`; neu khong, `tempfile.gettempdir()` co
the loi va endpoint crawler tra 500 du health van xanh.

Crawler la candidate-first: scan chi tao candidate pending va notification; khong
duoc import/embedding neu chua co quyet dinh approve cua admin.
