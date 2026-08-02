# Context map

What lives where, which way the arrows point, and where the rules cited in
code comments actually live.

## Modules (`app/<module>/`, layers `domain/ application/ infrastructure/ presentation/`)

| Module | Owns | Notes |
|---|---|---|
| `auth` | users, OTP + Google/Facebook login, JWT | The reference 4-layer example. `users` is the shared identity table for everything. |
| `me` | self-service profile, verified email change, own bookings/designs | Reuses auth's OTP repository for the email-change code. |
| `admin` | staff lifecycle, user administration, delete-or-anonymize | Writes most audit entries. |
| `branches` | `locations` table + public catalog views | Global (NULL-branch) services merge into every branch. |
| `services` | services, length extensions | `duration_min` is menu display only — the scheduler runs on the capability matrix. |
| `capability` | staff × service → real minutes | The single source of scheduling truth. Missing cell = never assign. Saves are per-technician replace. |
| `availability` | capacity ledger, slot finder, booking window | Lanes per 15-min slot per skill group; every expected tech is sellable (appointment-only, no reserve). |
| `bookings` | booking lifecycle, soft-lock expiry, pricing | State machine: PENDING → APPROVED → IN_PROGRESS → COMPLETED / CANCELLED / NO_SHOW. |
| `slot_locks` | manual time-range closures | Branch-wide (staff NULL) or per-staff; no TTL, deleted by hand. |
| `schedule` | staff/admin day view, pending work queue | Grid = deposit-secured; revenue aggregate admin-only. |
| `allocation` | Step A roster solve, Step B materialize, manual reassign | The 21:00 nightly close. `staff_day_assignments` unique `(staff_id, day)` keeps one tech at one salon per day; customer wishes (`preferred_staff_id`) are ignored by the allocator and granted by managers via `/allocation/reassign`. |
| `staff` | staff table, personal schedule, start-service | `branch_id` is only a home preference — techs belong to the chain. |
| `shifts` | `staff_rosters` CRUD | Display-only: the scheduling engine never reads it (days_off + Step A replaced published rosters). |
| `custom_designs` | design requests, quoting, storage | Accept path rides the normal booking pipeline. |
| `discounts` | discount rules | Only GIFT (threshold → message) has behavior today. |
| `webhooks` | payment transactions | HMAC-signed, idempotent; the only money entry point. |
| `notification` | SMS/email routing | console/null/live backends; email preferred (free); failures never break the business action. |
| `dashboard` | admin metrics | Revenue = successful deposits + finals − refunds. |
| `audit_log` | append-only action trail | Written directly by other modules; read by admins. |
| `shared` | settings, DB session, Redis, clock, rate limit, auth deps, middleware | `require_roles()` always admits ADMIN; role/status re-read from DB per request. |

## Dependency directions (the ones that matter)

- `availability` → `allocation` (`expected_staff`, `bookable_at`) →
  `capability` (`load_matrix`, `is_available`) → `services`/`staff` models.
  Kept one-way; `roster.py` duplicates the active-status list locally to
  avoid a cycle.
- `bookings.create` → availability (window + ledger), allocation
  (`expected_staff`), capability (preference checks), slot_locks, discounts
  (gift), custom_designs.
- `webhooks` → bookings + audit_log; `me`/`schedule`/`dashboard` read
  webhook transactions to answer "is the deposit paid".
- `shared.presentation.dependencies` → `auth` models (the shared layer is
  deliberately not auth-agnostic).
- Background jobs (in `app/main.py`, APScheduler + Redis NX locks):
  soft-lock expiry every 60s; nightly allocation at `BOOKING_CLOSE_HOUR`
  (21:00 shop time) plus a 21:20 watchdog; all idempotent.

## Data model (17 tables)

`users` ← `staff` (1:1) ← `staff_capabilities`, `staff_day_assignments`,
`staff_rosters`; `locations` ← `services` (nullable = global) ←
`service_extensions`; `bookings` ← `booking_details` (per-service legs,
`staff_id` NULL until materialize, `preferred_staff_id` records the wish) ←
`payment_transactions`; `custom_designs`, `discounts`,
`slot_locks`, `allocation_runs`, `audit_logs`. All enums are stored as
VARCHAR (no native PG enums), timestamps are UTC `timestamptz`, and the
important invariants are unique constraints: `(staff_id, day)` for day
assignments, `(staff_id, service_id)` for matrix cells,
`provider_transaction_id` for webhook idempotency. Hot query paths are
indexed as of migration `8c2e4b9f1a70`; the walk-in `queue_tickets` table was
dropped in `3d7f2c8a9e51` when the shop went appointment-only, and
`6b90e4d21f83` replaced the pin system with `preferred_staff_id`.

## Where the "design doc" citations live

Code comments cite a design document (v3.x) that is not checked into this
repository. Map of the citations to the code that implements them:

| Citation | Rule | Implemented in |
|---|---|---|
| 1.1 | capacity-ledger service attributes (skill_group, turn_weight, buffer) and rare-service cap | `services/infrastructure/models.py`, `availability/application/capacity.py` |
| 2.1 | 21:00 D-1 booking freeze; nightly allocation window | `shared/config/settings.py` (`BOOKING_CLOSE_HOUR`), `app/main.py`, `availability/application/capacity.py` |
| 3.3b (softened) | chain-level techs; a named tech is a note granted by managers, never by the allocator | `allocation/application/reassign.py`, `allocation/application/materialize.py`, `bookings/application/create.py` |
| 3.5 | turn fairness, derived (never stored) turn ledger | `allocation/application/materialize.py` |
| 4.3 | allocation runs as an append-only audit; human repair ladder | `allocation/infrastructure/models.py`, `allocation/application/nightly.py` |
| fix #4 | weekly `max_hours_week` guard at booking time | `bookings/application/create.py` |
| fix #8 | idempotent nightly run + watchdog rerun | `app/main.py`, `allocation/application/materialize.py` |
| edge case 10 | sick tech frees only any-tech legs | `allocation/application/materialize.py` (`release_staff_assignments`) |
