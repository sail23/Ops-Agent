from enum import Enum, auto


class AgentRole(str, Enum):
    OPS = "Ops-Agent"
    SRE = "SRE-Agent"
    CODE = "Code-Agent"
    REPORT = "Report-Agent"


class Permission(str, Enum):
    READ_METRICS = auto()
    APPROVE_PLAN = auto()
    GENERATE_CODE = auto()
    EXECUTE_SANDBOX = auto()
    AUDIT_ALL = auto()
    VETO = auto()


_ROLE_MATRIX: dict[AgentRole, frozenset[Permission]] = {
    AgentRole.OPS: frozenset({Permission.READ_METRICS}),
    AgentRole.SRE: frozenset({Permission.READ_METRICS, Permission.APPROVE_PLAN}),
    AgentRole.CODE: frozenset(
        {Permission.READ_METRICS, Permission.GENERATE_CODE, Permission.EXECUTE_SANDBOX}
    ),
    AgentRole.REPORT: frozenset(
        {Permission.READ_METRICS, Permission.AUDIT_ALL, Permission.VETO}
    ),
}


def assert_permission(role: AgentRole, need: Permission) -> None:
    if need not in _ROLE_MATRIX.get(role, frozenset()):
        raise PermissionError(f"{role.value} lacks permission {need.name}")
