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

## Ngôn ngữ

Trò chuyện với user bằng tiếng Việt. Nhưng mọi text xuất hiện "trên web" — response API, `detail` message của `HTTPException`, validator error message, docstring/label hiển thị ra ngoài, OpenAPI docs — **phải viết bằng tiếng Anh**. Code, tên biến, comment thì viết tiếng Anh như bình thường.

## Stack & quy ước

- FastAPI + SQLAlchemy 2.0 + Alembic + PostgreSQL (`psycopg`), Redis, `python-jose` (JWT), `bcrypt`.
- Kiến trúc theo module (`app/<module>/`) với 4 lớp: `domain/`, `application/`, `infrastructure/`, `presentation/` — xem `app/auth/` làm ví dụ tham chiếu.
- Không dùng `__init__.py` trong `app/` (namespace package). Chạy venv qua `venv/Scripts/python.exe`.
- `requirements.txt` được lưu ở encoding **UTF-16** (do `pip freeze > requirements.txt` chạy trong PowerShell 5.1) — khi sửa file này phải đọc/ghi đúng `encoding='utf-16'`, không dùng encoding mặc định.
- Test nằm ở `app/tests/` (không phải `tests/` gốc), dùng `fakeredis` để giả lập Redis và Postgres local thật cho DB — xem `app/tests/conftest.py`.

# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
