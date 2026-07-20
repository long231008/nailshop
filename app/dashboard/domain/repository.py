from abc import ABC, abstractmethod
from datetime import date, datetime
from uuid import UUID

from app.dashboard.domain.entities import DashboardSummary, RevenueSummary


class DashboardRepository(ABC):
    @abstractmethod
    def get_summary(self, branch_id: UUID | None, today: date) -> DashboardSummary:
        raise NotImplementedError

    @abstractmethod
    def get_revenue_summary(self, branch_id: UUID | None, now: datetime) -> RevenueSummary:
        raise NotImplementedError
