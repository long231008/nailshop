# nailshop

FastAPI backend cho hệ thống đặt lịch tiệm nail. Solo project cá nhân của long231008 — không có team, không có review process, remote `main` là nhánh làm việc trực tiếp.

## Cách làm việc

Chủ dự án muốn tốc độ, không muốn bị hỏi lại giữa chừng. Mặc định: **cứ làm, không dừng lại xin xác nhận**, kể cả với:
- `git add` / `git commit` / `git push` lên `origin/main`
- Cài thêm package vào `venv` khi cần (và cập nhật `requirements.txt` tương ứng)
- Chạy migration Alembic (`alembic revision --autogenerate`, `alembic upgrade head`) trên Postgres local
- Sửa/tạo file trong `app/`, `migrations/`, `app/tests/`
- Quyết định các chi tiết implementation nhỏ (tên biến, cấu trúc response, việc có thêm 1 cột DB hay không...) theo phán đoán hợp lý riêng, thay vì hỏi lại

Chỉ dừng lại hỏi khi thật sự cần quyết định sản phẩm không thể suy ra được (vd: đổi luồng nghiệp vụ, xoá dữ liệu thật trên môi trường không phải local, hoặc hành động gần như không thể đảo ngược và có hậu quả lớn).

## Stack & quy ước

- FastAPI + SQLAlchemy 2.0 + Alembic + PostgreSQL (`psycopg`), Redis, `python-jose` (JWT), `bcrypt`.
- Kiến trúc theo module (`app/<module>/`) với 4 lớp: `domain/`, `application/`, `infrastructure/`, `presentation/` — xem `app/auth/` làm ví dụ tham chiếu.
- Không dùng `__init__.py` trong `app/` (namespace package). Chạy venv qua `venv/Scripts/python.exe`.
- `requirements.txt` được lưu ở encoding **UTF-16** (do `pip freeze > requirements.txt` chạy trong PowerShell 5.1) — khi sửa file này phải đọc/ghi đúng `encoding='utf-16'`, không dùng encoding mặc định.
- Test nằm ở `app/tests/` (không phải `tests/` gốc), dùng `fakeredis` để giả lập Redis và Postgres local thật cho DB — xem `app/tests/conftest.py`.
