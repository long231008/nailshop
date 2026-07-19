from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel


class QueueScanRequest(BaseModel):
    branch_id: UUID


class QueueTicketResponse(BaseModel):
    id: UUID
    ticket_number: str
    branch_id: UUID
    status: str
    created_at: datetime


class QueueVipEntry(BaseModel):
    booking_id: UUID
    booking_date: date
    status: str


class QueueAdminResponse(BaseModel):
    vip: list[QueueVipEntry]
    walkin: list[QueueTicketResponse]


class PublicQueueEntry(BaseModel):
    ticket_number: str
    status: str
    position: int


class PublicQueueResponse(BaseModel):
    entries: list[PublicQueueEntry]
