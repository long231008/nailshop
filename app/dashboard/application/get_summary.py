from datetime import date
from uuid import UUID

from app.dashboard.domain.entities import DashboardSummary
from app.dashboard.domain.repository import DashboardRepository


def get_dashboard_summary(
    repository: DashboardRepository, branch_id: UUID | None, today: date
) -> DashboardSummary:
    return repository.get_summary(branch_id, today)
