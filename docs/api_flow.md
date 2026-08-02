# API flows

How the system's ~65 endpoints (18 routers, all mounted under `/app`) compose
into the four journeys that matter. The shop is appointment-only: there is no
walk-in queue, and every expected technician is sellable (no capacity reserve). Paths below include the `/app` prefix.
Role notes: `require_roles(...)` always also admits ADMIN, and role/status are
re-read from the database on every request, so blocking an account takes
effect immediately.

## 1. Customer books online

**Sign in (passwordless).** `POST /auth/request-otp` creates the account on
first use and sends a 6-digit code (5-min TTL, 60s resend cooldown, 5 wrong
attempts burn the code); the response is identical whether or not the
identifier is already registered. `POST /auth/verify` activates the account
and returns a JWT. `GET /auth/google/login|callback` and the optional
Facebook pair do the same via OAuth, delivering the token to
`FRONTEND_URL/auth/callback` in the URL fragment.

**Discover (public, no auth).** `GET /branches` (each branch embeds its
services plus the global NULL-branch ones), `GET /services`,
`GET /slot-locks/public` (branch-wide closures, shown crossed out),
`GET /discounts/gift-preview`.

**Pick a time.** `GET /availability?branch_id&date&service_ids[&staff_id]`
(public). The window gate answers `closed_day` (Sundays — arranged privately
via Facebook), `too_far` (beyond 14 days), or `closed` (same-day, or past
21:00 local on D-1 — the freeze that lets the nightly allocation work on
fixed demand). Any-tech slots are sold against the capacity ledger: one lane
= one overlapping customer per 15-minute slot per skill group, capped at the
number of expected techs of that group (full capacity - appointment-only, no
reserve); leg length is the slowest eligible tech's real minutes (capability
matrix), grid-snapped.
A preferred technician does not change which times are sellable - the wish
is recorded on the booking and honoured at the nightly allocation. Candidate
starts run 09:00–17:30 inclusive; a 17:30 start may run past 18:00 closing
by explicit choice.

**Create.** `POST /bookings` (CUSTOMER). Per-branch advisory lock serializes
the capacity check against the insert. Items run back-to-back on one chair
from the first start time. Per-customer cap: ≤120 booked minutes per day.
Naming a tech records a *preference* (`preferred_staff_id`) — a wish the
nightly allocation seats first whenever that tech's timeline allows, never a
promise; wishes that cannot come true (tech off that day, or lacking the
capability cell) are rejected up front. A custom design must be owned, PRICED, and
attached to a nail-art (`category == "addon"`) service; its quote replaces
the service price and the design flips to ACCEPTED. Result: booking PENDING,
plus a `gift_message` when an active GIFT discount threshold is beaten.

**Approve → deposit.** `POST /bookings/{id}/approve` (STAFF): PENDING →
APPROVED, deposit = 30% of total (HALF_UP), `approved_at` stamped, customer
receives a deposit link, and a 15-minute soft lock starts. The truth of the
lock lives in `approved_at` in Postgres — the Redis key
`booking:soft_lock:{id}` is only a fast-path marker.

**Pay.** The provider calls `POST /webhooks/payment` signed with
HMAC-SHA256 over the raw body (`X-Signature`). Idempotent on
`provider_transaction_id`; a replay reports the stored outcome
(`processed: true` for an already-recorded success). A "successful" payment
below the amount due — or a deposit for a booking that was never approved
(no deposit due yet) — is recorded as FAILED and rejected. A valid deposit
arriving while the booking is still PENDING (with `deposit_amount` known)
auto-approves it. On success the soft-lock marker is deleted and the
customer is notified.

**Expire.** A scheduler job sweeps every 60s (Redis NX lock so one replica
runs per tick): APPROVED bookings older than 15 minutes with no successful
deposit are CANCELLED, their designs released back to PRICED, the customer
notified.

**Self-service.** `GET /me/bookings` regenerates deposit links for
approved-but-unpaid bookings. `POST /bookings/{id}/cancel` (owner only,
PENDING/APPROVED, ≥2h notice) releases designs; the audit entry records
whether a paid deposit is now owed back (there is no automatic refund —
refunds arrive later as `transaction_type=refund` webhooks and are
subtracted from dashboard revenue).

**Finish.** `POST /bookings/{id}/complete` (STAFF) completes one leg; the
booking completes when all legs are done. Admin-only:
`POST /bookings/{id}/no-show`, `POST /bookings/{id}/complete-manual`,
`POST /bookings/{id}/final-price` (COMPLETED only), `GET /bookings/{id}/bill`
(total/deposit/remaining). The balance settles via a second webhook with
`transaction_type=final_payment` (expected = (final_price or total_price) −
deposit).

## 2. Custom design: request → quote → book

1. `POST /custom-designs` (CUSTOMER, 10/hour per IP, multipart): photo
   and/or description, ≤8 MB, image content types only, max 10 open requests
   per customer. Stored in Cloudinary (or local `/uploads` fallback when
   Cloudinary is unconfigured). Status PENDING.
2. `GET /custom-designs?priced=false` (ADMIN) is the triage list; the
   dashboard counts unpriced designs.
3. `POST /custom-designs/{id}/price` (ADMIN, or STAFF holding the delegated
   `can_price_custom_designs` flag): sets the quote, status PRICED, customer
   notified on their signup channel.
4. `POST /custom-designs/{id}/accept` (owner): builds a one-item booking and
   rides the normal booking path of flow 1 — nail-art service only, quote
   replaces the price, design ACCEPTED. `POST /custom-designs/{id}/reject`
   declines. Cancelling or expiring the booking returns the design to PRICED
   so it can be rebooked.
5. The tech sees the design image and description inline on the day schedule.

## 3. Technician daily cycle

**21:00 close (cron + 21:20 watchdog, Redis-guarded, idempotent).**
`run_nightly_allocation` targets tomorrow (skips Sundays):

- **Step A `solve_day`** places every active, available tech at exactly one
  branch: non-floating techs go home, floating techs go where uncovered
  demand in their skill groups is largest.
- **Step B `materialize_day`** (per branch, advisory-locked, idempotent)
  names a tech for every leg — the customer's preferred tech first when
  their timeline (including manual slot locks) allows, then fewest turns,
  same-booking continuity, customer affinity, and longest idle — and
  shrinks the leg from the cautious planning hold to the tech's real
  minutes, so timelines show when each tech is really free. Legs nobody can
  serve stay unassigned and are logged for a human: the repair ladder is
  deliberately not automated.

**Repair.** `POST /allocation/run` (ADMIN) re-runs Step A + materialize;
`release_staff_id` first frees all of a sick tech's legs (named techs are
preferences, not promises, so nothing needs a phone call first).
`GET /allocation/status` shows runs, the day roster, and unassigned legs.

**The day.** `GET /schedule?date=` (STAFF): deposit-secured appointments on
the grid, the rest split into awaiting_approval / awaiting_deposit; the
aggregate expected value is admin-only. `GET /schedule/pending` lists all
future actionable bookings. `GET /staff/{id}/schedule` (self or admin) is
the personal 14-day view. `POST /staff/{id}/start-service` marks a leg
IN_PROGRESS (one customer at a time per tech);
`POST /bookings/{id}/complete` finishes legs. Staff with `can_lock_slots`
manage slot locks at their own branch.

## 4. Admin loop

- **Dashboard**: `GET /dashboard/summary` (today's counts, unpriced
  designs, headcount) and `GET /dashboard/revenue` (deposit vs total for
  today/week/month/year; refunds subtracted from totals).
- **Booking desk**: `GET /bookings` with filters; approve / no-show /
  complete-manual / final-price / bill; `GET /schedule/pending` as the work
  queue.
- **Capacity configuration** — the heart of the system:
  `GET/PUT /capability/matrix` upserts the price list, per-tech
  `days_off`/`max_hours_week`, and the capability matrix (real minutes per
  tech per service; a missing cell means "never assign"). Saves replace one
  technician's row at a time — staff absent from the payload keep their
  cells. Validation: 5–240 minutes and ≤3× menu duration. The save reports
  services nobody can perform and future pinned bookings whose cell was
  removed, for reception to phone.
- **Staffing**: `POST /admin/staff`, grant/revoke staff on users, block
  (clears future rosters, unassigns future pending legs), activate, reserve,
  and delegation of the slot-lock and design-pricing flags.
- **Users**: search with booking counts, per-user history, PATCH
  role/status, DELETE with delete-or-anonymize semantics (history-bearing
  accounts are anonymized and blocked, never erased).
- **Catalog**: branches, services + length extensions, discounts (GIFT
  threshold rules are the only type applied today).
- **Audit**: `GET /audit-log` (ADMIN) — bookings, staff lifecycle, slot
  locks, deletions, webhook auto-approvals.
