from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class Priority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    BLOCKED = "blocked"


@dataclass
class WorkItem:
    ticket_id: str
    title: str
    owner: str
    priority: Priority
    estimate_hours: float
    due_on: date
    dependencies: list[str]


@dataclass
class SprintPlan:
    name: str
    starts_on: date
    ends_on: date
    capacity_hours: float
    items: list[WorkItem]


def normalize_owner(owner: str) -> str:
    return " ".join(owner.strip().lower().split())


def dependency_map(items: list[WorkItem]) -> dict[str, set[str]]:
    return {
        item.ticket_id: {dependency.strip().upper() for dependency in item.dependencies}
        for item in items
    }


def blocked_items(items: list[WorkItem], completed_ticket_ids: set[str]) -> list[WorkItem]:
    completed = {ticket_id.strip().upper() for ticket_id in completed_ticket_ids}
    return [
        item
        for item in items
        if any(dependency.strip().upper() not in completed for dependency in item.dependencies)
    ]


def owner_load(items: list[WorkItem]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for item in items:
        owner = normalize_owner(item.owner)
        totals[owner] = totals.get(owner, 0.0) + item.estimate_hours
    return totals


class SprintRiskAnalyzer:
    def __init__(self, overload_threshold: float = 32.0):
        self.overload_threshold = overload_threshold

    def capacity_ratio(self, plan: SprintPlan) -> float:
        estimated = sum(item.estimate_hours for item in plan.items)
        if plan.capacity_hours <= 0:
            return 1.0
        return estimated / plan.capacity_hours

    def overdue_items(self, plan: SprintPlan, today: date) -> list[WorkItem]:
        return [
            item
            for item in plan.items
            if item.due_on < today and item.priority != Priority.BLOCKED
        ]

    def overloaded_owners(self, plan: SprintPlan) -> dict[str, float]:
        loads = owner_load(plan.items)
        return {
            owner: hours
            for owner, hours in loads.items()
            if hours > self.overload_threshold
        }

    def risk_score(self, plan: SprintPlan, completed_ticket_ids: set[str], today: date) -> float:
        ratio = self.capacity_ratio(plan)
        blocked_count = len(blocked_items(plan.items, completed_ticket_ids))
        overdue_count = len(self.overdue_items(plan, today))
        overloaded_count = len(self.overloaded_owners(plan))

        score = 0.0
        if ratio > 1.0:
            score += min((ratio - 1.0) * 0.6, 0.3)
        score += min(blocked_count * 0.08, 0.25)
        score += min(overdue_count * 0.1, 0.25)
        score += min(overloaded_count * 0.1, 0.2)
        return min(score, 1.0)


class ReleaseBrief:
    def __init__(self, analyzer: SprintRiskAnalyzer):
        self.analyzer = analyzer

    def summarize(self, plan: SprintPlan, completed_ticket_ids: set[str], today: date) -> dict[str, object]:
        blockers = blocked_items(plan.items, completed_ticket_ids)
        overdue = self.analyzer.overdue_items(plan, today)
        overloaded = self.analyzer.overloaded_owners(plan)

        return {
            "sprint": plan.name,
            "risk_score": round(self.analyzer.risk_score(plan, completed_ticket_ids, today), 3),
            "blocked_tickets": [item.ticket_id for item in blockers],
            "overdue_tickets": [item.ticket_id for item in overdue],
            "overloaded_owners": sorted(overloaded),
        }


def build_release_brief(plan: SprintPlan, completed_ticket_ids: set[str], today: date) -> dict[str, object]:
    analyzer = SprintRiskAnalyzer()
    brief = ReleaseBrief(analyzer)
    return brief.summarize(plan, completed_ticket_ids, today)
