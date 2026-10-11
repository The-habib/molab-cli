"""
Rich CLI interface for molabctl.
Modular command architecture with domain-specific command handlers.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

import click
from rich.console import Console

# Backward-compatibility re-exports for external consumers and tests
from molab_cli.auth import inspect_auth_status
from molab_cli.commands import register_all_commands
from molab_cli.commands.agent import (
    cmd_chat,
    cmd_dashboard,
    cmd_gallery_download,
    cmd_gallery_info,
    cmd_gallery_list,
    cmd_gallery_search,
    cmd_mcp,
    cmd_ui,
    cmd_web,
    gallery_group,
)
from molab_cli.commands.compute import (
    cmd_compute,
    cmd_connections,
    cmd_create,
    cmd_delete,
    cmd_free,
    cmd_gpu,
    cmd_inspect,
    cmd_list,
    cmd_ps,
    cmd_remote_env,
    cmd_stop,
    cmd_thumbnail,
    cmd_usage,
)
from molab_cli.commands.files import (
    cmd_cat,
    cmd_clone,
    cmd_download,
    cmd_export_html,
    cmd_export_ipynb,
    cmd_export_md,
    cmd_export_pdf,
    cmd_export_script,
    cmd_file_cat,
    cmd_file_cp,
    cmd_file_info,
    cmd_file_ls,
    cmd_file_mv,
    cmd_file_rm,
    cmd_file_search,
    cmd_pull,
    cmd_push,
    cmd_rename,
    cmd_snapshot_create,
    cmd_snapshot_delete,
    cmd_snapshot_list,
    cmd_snapshot_restore,
    cmd_storage_git_clone,
    cmd_storage_hf_pull,
    cmd_storage_hf_push,
    cmd_storage_rclone_backup,
    cmd_storage_rclone_config,
    cmd_storage_rclone_restore,
    cmd_sync,
    cmd_vault_inspect,
    cmd_vault_pack,
    cmd_vault_unpack,
    export_group,
    file_group,
    reconstruct_script,
    snapshot_group,
    storage_group,
    vault_group,
)
from molab_cli.commands.gateway import (
    bridge_group,
    cmd_analytics,
    cmd_bridge_start,
    cmd_bridge_status,
    cmd_bridge_stop,
    cmd_credentials,
    cmd_creds,
    cmd_deploy,
    cmd_forward,
    cmd_keys_create,
    cmd_keys_list,
    cmd_keys_revoke,
    cmd_perf,
    cmd_public,
    cmd_serve_logs,
    cmd_serve_status,
    cmd_serve_stop,
    cmd_share,
    cmd_stats,
    keys_group,
    serve_group,
)
from molab_cli.commands.jobs import (
    batch_group,
    cmd_batch_cancel,
    cmd_batch_list,
    cmd_batch_logs,
    cmd_batch_retry,
    cmd_batch_run,
    cmd_batch_status,
    cmd_batch_submit,
    cmd_batch_validate,
    cmd_job_artifacts,
    cmd_job_cancel,
    cmd_job_list,
    cmd_job_logs,
    cmd_job_status,
    cmd_job_submit,
    cmd_notify_test,
    cmd_workload_list,
    job_group,
    notify_group,
    workload_group,
)
from molab_cli.commands.system import (
    cmd_capabilities,
    cmd_doctor,
    cmd_exec,
    cmd_install,
    cmd_keepalive_list,
    cmd_keepalive_logs,
    cmd_keepalive_run,
    cmd_keepalive_start,
    cmd_keepalive_status,
    cmd_keepalive_stop,
    cmd_kernel_eval,
    cmd_kernel_interrupt,
    cmd_kernel_restart,
    cmd_kernel_status,
    cmd_login,
    cmd_permanent,
    cmd_pkg_add,
    cmd_pkg_list,
    cmd_shell,
    cmd_status,
    keepalive_group,
    kernel_group,
    pkg_group,
)

console = Console()


@click.group(invoke_without_command=True)
@click.version_option(version="2.5.0", prog_name="molab")
@click.option("--debug", is_flag=True, help="Enable verbose debug diagnostics and trace logging")
@click.pass_context
def cli(ctx, debug: bool = False):
    """molab: Modern interactive CLI & cloud orchestrator for MoLab with NVIDIA Blackwell GPU support."""
    if debug:
        os.environ["MOLAB_DEBUG"] = "1"
        from molab_cli.logger import setup_logger
        setup_logger()
    if ctx.invoked_subcommand is None:
        from molab_cli.tui import start_interactive_tui
        start_interactive_tui()


# Register all modular domain commands
register_all_commands(cli)


def main():
    cli()


if __name__ == "__main__":
    main()
