from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class SessionState(enum.StrEnum):
    OPEN = "open"
    INTEGRATING = "integrating"
    FINALIZED = "finalized"
    ABORTED = "aborted"


class AgentState(enum.StrEnum):
    ACTIVE = "active"
    KILLED = "killed"


class ProposalState(enum.StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    REVISE = "revise"
    CONFLICT = "conflict"
    INTEGRATED = "integrated"


@dataclass
class Session:
    id: str
    feature: str
    feature_key: str
    base_sha: str
    integration_branch: str
    integration_worktree: str
    target_branch: str = "main"
    state: SessionState = SessionState.OPEN
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "feature": self.feature,
            "feature_key": self.feature_key,
            "base_sha": self.base_sha,
            "integration_branch": self.integration_branch,
            "integration_worktree": self.integration_worktree,
            "target_branch": self.target_branch,
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Session:
        return cls(
            id=d["id"],
            feature=d["feature"],
            feature_key=d.get("feature_key", d.get("feature", "")),
            base_sha=d["base_sha"],
            integration_branch=d["integration_branch"],
            integration_worktree=d["integration_worktree"],
            target_branch=d.get("target_branch", "main"),
            state=SessionState(d.get("state", "open")),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )


@dataclass
class Agent:
    id: str
    role: str
    base_sha: str
    base_ref: str
    worktree: str
    state: AgentState = AgentState.ACTIVE
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "base_sha": self.base_sha,
            "base_ref": self.base_ref,
            "worktree": self.worktree,
            "state": self.state.value,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Agent:
        return cls(
            id=d["id"],
            role=d.get("role", ""),
            base_sha=d["base_sha"],
            base_ref=d.get("base_ref", d["base_sha"]),
            worktree=d["worktree"],
            state=AgentState(d.get("state", "active")),
            created_at=d.get("created_at", ""),
        )


@dataclass
class Proposal:
    id: str
    agent_id: str
    base_sha: str
    title: str
    files: list[str] = field(default_factory=list)
    summary: str = ""
    confidence: float | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "base_sha": self.base_sha,
            "title": self.title,
            "files": list(self.files),
            "summary": self.summary,
            "confidence": self.confidence,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Proposal:
        return cls(
            id=d["id"],
            agent_id=d["agent_id"],
            base_sha=d["base_sha"],
            title=d["title"],
            files=list(d.get("files", [])),
            summary=d.get("summary", ""),
            confidence=d.get("confidence"),
            created_at=d.get("created_at", ""),
        )


@dataclass
class Review:
    state: ProposalState = ProposalState.PENDING
    feedback: str = ""
    reason: str = ""
    integrated: bool = False
    integration_sha: str | None = None
    decided_at: str = ""
    applied_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "feedback": self.feedback,
            "reason": self.reason,
            "integrated": self.integrated,
            "integration_sha": self.integration_sha,
            "decided_at": self.decided_at,
            "applied_at": self.applied_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Review:
        return cls(
            state=ProposalState(d.get("state", "pending")),
            feedback=d.get("feedback", ""),
            reason=d.get("reason", ""),
            integrated=d.get("integrated", False),
            integration_sha=d.get("integration_sha"),
            decided_at=d.get("decided_at", ""),
            applied_at=d.get("applied_at", ""),
        )
