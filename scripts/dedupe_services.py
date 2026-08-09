"""Merge duplicate service rows into one list for the whole chain.

Rows counting as duplicates: same name (case-insensitive). For each group the
keeper is the copy bookings already reference (history must keep its row), or
else the copy with the most filled-in data. Everything the other copies know -
capability minutes, length options, discounts - is moved onto the keeper
before they are deleted, so nothing typed in is lost. A group whose copies sat
at different salons becomes chain-wide; whether each salon actually offers it
then follows from its chairs/beds and rostered technicians, as everywhere else.

Dry run by default - prints the plan and changes nothing:

    python scripts/dedupe_services.py

Apply for real:

    python scripts/dedupe_services.py --apply
"""

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import Session  # noqa: E402

from app.bookings.infrastructure.models import BookingDetailModel  # noqa: E402
from app.capability.infrastructure.models import StaffCapabilityModel  # noqa: E402
from app.discounts.infrastructure.models import DiscountModel  # noqa: E402
from app.services.infrastructure.models import (  # noqa: E402
    ServiceExtensionModel,
    ServiceModel,
)


def dedupe(db: Session, apply: bool = False) -> list[str]:
    """Merge duplicates; returns the human-readable report. Commits only when
    apply is True - a dry run rolls every change back."""
    report: list[str] = []

    services = db.query(ServiceModel).order_by(ServiceModel.created_at).all()
    groups: dict[str, list[ServiceModel]] = defaultdict(list)
    for service in services:
        groups[service.name.strip().casefold()].append(service)

    booked_ids = {row[0] for row in db.query(BookingDetailModel.service_id).distinct()}
    cells: dict = defaultdict(dict)  # service_id -> staff_id -> cell row
    for cell in db.query(StaffCapabilityModel).all():
        cells[cell.service_id][cell.staff_id] = cell
    lengths: dict = defaultdict(list)
    for extension in db.query(ServiceExtensionModel).all():
        lengths[extension.service_id].append(extension)

    for rows in groups.values():
        if len(rows) < 2:
            continue
        display_name = rows[0].name

        booked = [r for r in rows if r.id in booked_ids]
        if len(booked) > 1:
            report.append(
                f"SKIP  {display_name!r}: {len(booked)} copies already carry bookings - "
                "these need a human decision"
            )
            continue

        if booked:
            keeper = booked[0]
        else:
            # The most filled-in copy survives; ties prefer more lengths, then
            # an already chain-wide row, then the oldest.
            keeper = max(
                rows,
                key=lambda r: (
                    len(cells[r.id]),
                    len(lengths[r.id]),
                    r.branch_id is None,
                    -r.created_at.timestamp(),
                ),
            )
        losers = [r for r in rows if r.id != keeper.id]

        moved_cells = 0
        moved_lengths = 0
        for loser in losers:
            for staff_id, cell in list(cells[loser.id].items()):
                if staff_id not in cells[keeper.id]:
                    cell.service_id = keeper.id
                    cells[keeper.id][staff_id] = cell
                    del cells[loser.id][staff_id]
                    moved_cells += 1
            keeper_length_names = {e.name.casefold() for e in lengths[keeper.id]}
            for extension in lengths[loser.id]:
                if extension.name.casefold() not in keeper_length_names:
                    extension.service_id = keeper.id
                    lengths[keeper.id].append(extension)
                    keeper_length_names.add(extension.name.casefold())
                    moved_lengths += 1
                else:
                    db.delete(extension)
            db.query(DiscountModel).filter(DiscountModel.service_id == loser.id).update(
                {"service_id": keeper.id}, synchronize_session=False
            )
            # The models carry no relationship() between service and its
            # extensions/cells, so nothing tells the unit of work to write the
            # moves before this delete - flush them down explicitly first.
            db.flush()
            db.delete(loser)  # remaining capability cells cascade away

        if len({r.branch_id for r in rows}) > 1:
            keeper.branch_id = None  # copies from several salons -> one chain-wide row

        where = "whole chain" if keeper.branch_id is None else "its salon"
        report.append(
            f"MERGE {display_name!r}: kept the copy with {len(cells[keeper.id])} filled "
            f"cell(s) ({where}), moved {moved_cells} cell(s) and {moved_lengths} length(s), "
            f"deleted {len(losers)} duplicate(s)"
        )

    if apply:
        db.commit()
    else:
        db.rollback()
    return report


if __name__ == "__main__":
    from app.shared.infrastructure.database.session import SessionLocal

    apply_changes = "--apply" in sys.argv
    session = SessionLocal()
    try:
        lines = dedupe(session, apply=apply_changes)
    finally:
        session.close()

    if not lines:
        print("No duplicated service names found - nothing to do.")
    else:
        for line in lines:
            print(line)
        if apply_changes:
            print(f"\nDone: {sum(1 for line in lines if line.startswith('MERGE'))} group(s) merged.")
        else:
            print("\nDry run only - nothing was changed. Re-run with --apply to make it real.")
