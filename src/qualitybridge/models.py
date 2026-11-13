from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SuiteConfig(StrictModel):
    paths: list[str] = Field(default_factory=list)
    filter: str | None = None

    @field_validator("paths")
    @classmethod
    def validate_paths(cls, values: list[str]) -> list[str]:
        if any(not v.strip() or v.startswith("-") or "\x00" in v for v in values):
            raise ValueError("Os seletores de testes devem ser não vazios e não iniciar com '-'.")
        return values


class DockerConfig(StrictModel):
    image: str = Field(min_length=1)
    network: Literal["none", "bridge"] = "none"


class ProjectConfig(StrictModel):
    version: Literal[1] = 1
    project: str = Field(min_length=1)
    runner: Literal["pytest", "playwright", "jest"]
    root: str = "."
    output_dir: str = ".qualitybridge"
    timeout_seconds: int = Field(default=300, ge=1, le=86400)
    python: str | None = None
    node: str = "node"
    env: dict[str, str] = Field(default_factory=dict)
    suites: dict[str, SuiteConfig] = Field(default_factory=lambda: {"full": SuiteConfig()})
    docker: DockerConfig | None = None

    @field_validator("suites")
    @classmethod
    def require_full(cls, value: dict[str, SuiteConfig]) -> dict[str, SuiteConfig]:
        if "full" not in value:
            raise ValueError("A suíte 'full' é obrigatória.")
        return value

    @field_validator("env")
    @classmethod
    def valid_environment(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not k or "=" in k or "\x00" in k + v for k, v in value.items()):
            raise ValueError("Variável de ambiente inválida.")
        return value


class TestCase(StrictModel):
    name: str
    classname: str = ""
    file_path: str = ""
    status: Literal["passed", "failed", "error", "skipped"]
    duration_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    message: str = ""
    stack_trace: str = ""


class RunResult(StrictModel):
    schema_version: int = 1
    id: str
    project: str
    runner: str
    suite: str
    started_at: datetime
    duration_seconds: float
    status: Literal["passed", "failed", "error", "timeout", "cancelled", "no_tests"]
    exit_code: int | None
    command: list[str]
    cases: list[TestCase] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)

    @property
    def counts(self) -> dict[str, int]:
        return {
            state: sum(t.status == state for t in self.cases)
            for state in ("passed", "failed", "error", "skipped")
        }
