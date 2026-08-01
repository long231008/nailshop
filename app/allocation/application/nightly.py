"""The nightly close (doc 2.1): at 21:00 local time, tomorrow stops taking
bookings (the capacity engine enforces that by the clock alone) and this job
runs Step B - materialize - for every branch, turning "the salon will assign a
suitable technician" into a name on each booking."""

import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from app.allocation.application.materialize import materialize_day
from app.allocation.application.roster import solve_day
from app.allocation.infrastructure.models import AllocationRunModel
from app.branches.infrastructure.models import LocationModel
from app.shared.infrastructure.clock import is_closed_day, today_in_shop_tz

logger = logging.getLogger(__name__)


def run_nightly_allocation(db: Session) -> list[AllocationRunModel]:
    target_date = today_in_shop_tz() + timedelta(days=1)
    if is_closed_day(target_date):
        logger.info("Nightly allocation: %s is a closed day, nothing to assign", target_date)
        return []

    # Step A: place every technician at a branch for tomorrow (pins fixed,
    # demand-driven greedy for the rest - doc 4.2/4.4).
    assignments = solve_day(db, target_date)
    logger.info(
        "Step A placed %d technicians for %s", len(assignments), target_date
    )

    runs = []
    for branch in db.query(LocationModel).all():
        run = materialize_day(db, branch.id, target_date)
        logger.info(
            "Nightly allocation %s branch=%s assigned=%d unassigned=%d",
            target_date,
            branch.id,
            run.assigned_count,
            run.unassigned_count,
        )
        if run.unassigned_count:
            # The three-tier repair ladder (doc 4.3) is a human decision here:
            # surface it loudly for the manager instead of silently juggling.
            logger.warning(
                "Allocation left %d unassigned legs at branch %s on %s - "
                "manager attention needed",
                run.unassigned_count,
                branch.id,
                target_date,
            )
        runs.append(run)
    return runs
