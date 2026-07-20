from abc import ABC, abstractmethod
from datetime import date
from uuid import UUID

from app.dashboard.domain.entities import DashboardSummary


class DashboardRepository(ABC):
    @abstractmethod
    def get_summary(self, branch_id: UUID | None, today: date) -> DashboardSummary:
        raise NotImplementedError
