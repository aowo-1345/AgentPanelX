"""Installed CLI for user-level Project and Feature workspace management."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from agentplanex.bootstrap import create_workspace
from agentplanex.domains.workspace import (
    BoardFeature,
    FeatureState,
    FeatureView,
    ManagedProject,
    ProjectBoard,
)
from agentplanex.infrastructure.workspace_git import WorkspaceGitError
from agentplanex.project_runtime.errors import FeatureBusyError
from agentplanex.services.workspace.errors import WorkspaceSchedulingError
from agentplanex.services.workspace.service import WorkspaceService
from agentplanex.settings import load_settings, resolve_settings_path


def main(argv: Sequence[str] | None = None) -> int:
    """Execute one Project or Feature command against the configured Workspace."""
    args = _parser().parse_args(argv)
    if args.resource == "auto-control":
        from agentplanex.services.auto_takeover._cli import run_control

        return run_control(args)
    if args.resource == "auto-owner-fork":
        from agentplanex.services.auto_takeover._cli import run_owner_fork

        return run_owner_fork(args)
    workspace: WorkspaceService | None = None
    try:
        settings_path = resolve_settings_path(args.config)
        workspace = create_workspace(
            load_settings(settings_path),
            settings_path=settings_path,
        )
        result = _dispatch(workspace, args)
    except (
        LookupError,
        ValueError,
        WorkspaceGitError,
        FeatureBusyError,
        WorkspaceSchedulingError,
    ) as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        if workspace is not None:
            workspace.close()
    print(json.dumps(result, ensure_ascii=False), file=sys.stdout)
    return 0


def _dispatch(workspace: WorkspaceService, args: argparse.Namespace) -> object:
    if args.resource == "project" and args.command == "register":
        return _project_json(
            workspace.register_project(
                name=args.name,
                repository_path=args.repository,
                main_branch=args.main_branch,
            )
        )
    if args.resource == "project" and args.command == "list":
        return [_project_json(project) for project in workspace.list_projects()]
    if args.resource == "feature" and args.command == "create":
        return _feature_json(
            workspace.create_feature(project_id=args.project, name=args.name)
        )
    if args.resource == "feature" and args.command == "begin":
        return _feature_state_json(
            workspace.begin_feature(
                project_id=args.project,
                triage_id=args.feature,
            )
        )
    if args.resource == "feature" and args.command == "list":
        return [
            _feature_json(feature)
            for feature in workspace.list_features(args.project)
        ]
    if args.resource == "board":
        return _project_board_json(workspace.project_board(args.project))
    raise AssertionError(f"Unhandled Workspace command: {args.resource} {args.command}")


def _project_json(project: ManagedProject) -> dict[str, str]:
    return {
        "project_id": project.project_id,
        "name": project.name,
        "repository_path": str(project.repository_path),
        "main_branch": project.main_branch,
    }


def _feature_json(feature: FeatureView) -> dict[str, str]:
    return {
        "triage_id": feature.triage_id,
        "project_id": feature.project_id,
        "name": feature.name,
        "branch": feature.branch,
        "worktree_path": str(feature.worktree_path),
    }


def _feature_state_json(state: FeatureState) -> dict[str, str]:
    return {
        "project_id": state.project_id,
        "triage_id": state.triage_id,
        "status": state.status,
    }


def _board_feature_json(feature: BoardFeature) -> dict[str, str]:
    return {
        "triage_id": feature.triage_id,
        "project_id": feature.project_id,
        "name": feature.name,
        "branch": feature.branch,
        "worktree_path": str(feature.worktree_path),
        "status": feature.status,
    }


def _project_board_json(board: ProjectBoard) -> dict[str, object]:
    return {
        "project_id": board.project_id,
        "name": board.name,
        "features": [_board_feature_json(feature) for feature in board.features],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentplanex",
        description="Manage AgentPlaneX Projects and Feature Runtimes",
    )
    parser.add_argument("--config", type=Path)
    resources = parser.add_subparsers(dest="resource", required=True)

    projects = resources.add_parser("project", help="manage registered Git Projects")
    project_commands = projects.add_subparsers(dest="command", required=True)
    register = project_commands.add_parser(
        "register",
        help="register one existing local Git repository",
    )
    register.add_argument("--name", required=True)
    register.add_argument("--repository", type=Path, required=True)
    register.add_argument("--main-branch", required=True)
    project_commands.add_parser("list", help="list registered Projects")

    features = resources.add_parser("feature", help="manage Project Features")
    feature_commands = features.add_subparsers(dest="command", required=True)
    create = feature_commands.add_parser(
        "create",
        help="create one attached Feature worktree and Runtime",
    )
    create.add_argument("--project", required=True)
    create.add_argument("--name", required=True)
    begin = feature_commands.add_parser(
        "begin",
        help="move one initialized Feature from TRIAGE to TODO",
    )
    begin.add_argument("--project", required=True)
    begin.add_argument("--feature", required=True, help="Feature Triage ID")
    feature_list = feature_commands.add_parser(
        "list",
        help="list Features registered for one Project",
    )
    feature_list.add_argument("--project", required=True)

    board = resources.add_parser(
        "board",
        help="show live Runtime status for one Project's Features",
    )
    board.add_argument("--project", required=True)

    control = resources.add_parser(
        "auto-control",
        help="internal fenced Runtime control for AutoTakeover",
    )
    control.add_argument("--cwd", type=Path, required=True)
    control.add_argument("--takeover-fence", required=True)
    control.add_argument("--print", dest="print_mode", action="store_true")
    control.add_argument("action", nargs=argparse.REMAINDER)

    owner_fork = resources.add_parser(
        "auto-owner-fork",
        help="internal Historical Owner fork for AutoTakeover",
    )
    owner_fork.add_argument("--cwd", type=Path, required=True)
    owner_fork.add_argument("--message-id", required=True)
    owner_fork.add_argument("--summary-id")
    owner_fork.add_argument("--print-context", action="store_true")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
