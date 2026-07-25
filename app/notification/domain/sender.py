from abc import ABC, abstractmethod

from app.notification.domain.notification import Notification


class NotificationSender(ABC):
    @abstractmethod
    def send(self, notification: Notification) -> None:
        raise NotImplementedError
