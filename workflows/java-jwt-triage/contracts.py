"""Executable triage output contract for auth0/java-jwt bug localization."""

from typing import Literal

from pydantic import BaseModel, Field

Subsystem = Literal[
    "core-verifier",
    "core-token",
    "core-creator",
    "algorithms",
    "impl-codec",
    "exceptions",
    "interfaces",
]


class BugTriageReport(BaseModel):
    """Canonical structured triage verdict identifying subsystem and root-cause Java file."""

    subsystem: Subsystem = Field(description="Logical auth0/java-jwt subsystem containing the bug.")
    suspected_file: str = Field(
        description="Exact repository-relative .java file path (e.g. lib/src/main/java/com/auth0/jwt/JWTVerifier.java)."
    )
    rationale: str = Field(description="Concise technical link between symptom and target Java class.")
