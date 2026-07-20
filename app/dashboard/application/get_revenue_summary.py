from datetime import datetime
from uuid import UUID

from app.dashboard.domain.entities import RevenueSummary
from app.dashboard.domain.repository import DashboardRepository


def get_revenue_summary(
    repository: DashboardRepository, branch_id: UUID | None, now: datetime
) -> RevenueSummary:
    return repository.get_revenue_summary(branch_id, now)
