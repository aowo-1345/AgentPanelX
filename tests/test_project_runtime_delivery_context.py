"""Final command-side ownership through one Feature Runtime Context."""

from dataclasses import fields

import agentplanex.services as services
from agentplanex.project_runtime.executions.base import ProjectExecutionDependencies
from agentplanex.services.delivery import DeliveryService
from agentplanex.services.project_runtime import ProjectRuntimeService


def test_delivery_and_executions_have_one_runtime_state_path() -> None:
    delivery_dependencies = {field.name for field in fields(DeliveryService)}
    execution_dependencies = {
        field.name for field in fields(ProjectExecutionDependencies)
    }

    assert "context" in delivery_dependencies
    assert delivery_dependencies.isdisjoint(
        {"database", "contexts", "owners", "runtime_contexts"}
    )
    assert "context" in execution_dependencies
    assert "runtime_contexts" not in execution_dependencies
    assert not hasattr(DeliveryService, "for_project")


def test_services_package_has_no_legacy_runtime_context_service() -> None:
    assert "RuntimeContextService" not in services.__all__
    assert not hasattr(services, "RuntimeContextService")


def test_command_orchestrator_does_not_own_read_projections() -> None:
    dependencies = {field.name for field in fields(ProjectRuntimeService)}

    assert "controls" not in dependencies
