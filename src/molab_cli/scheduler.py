"""
Autonomous Multi-Pod Resource-Aware Scheduler and Batch Orchestration Engine.
Features:
- Dynamic VRAM and GPU capability matching without hardcoded pod IDs or GPU names.
- Explainable pod selection auditing with explicit acceptance/rejection reasons.
- DAG dependency resolution and parallel execution tier calculation.
- Transactional SQLite coordination with retry policies and isolated webhook notifications.
"""

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from molab_cli.client import MoLabClient
from molab_cli.exceptions import BatchNotFoundError, SchedulingError, ValidationError
from molab_cli.jobs import JobManager
from molab_cli.notifications import NotificationDispatcher
from molab_cli.sandbox import SandboxSession
from molab_cli.workloads import WorkloadRegistry


@dataclass
class ValidationResult:
    """Result of validating a batch workload manifest."""
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    task_count: int = 0
    execution_order: List[List[str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "task_count": self.task_count,
            "execution_order": self.execution_order,
        }


@dataclass
class PodEvaluation:
    """Detailed audit of candidate pod suitability for a specific task."""
    pod_id: str
    eligible: bool
    reasons: List[str]
    score: float
    telemetry: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pod_id": self.pod_id,
            "eligible": self.eligible,
            "reasons": self.reasons,
            "score": round(self.score, 2),
            "telemetry": self.telemetry,
        }


def validate_manifest(
    manifest: Dict[str, Any],
    workload_registry: Optional[WorkloadRegistry] = None,
) -> ValidationResult:
    """
    Validate batch manifest schema, task definitions, and dependency DAG.
    Computes parallel topological stages using Kahn's algorithm.
    """
    errors: List[str] = []
    warnings: List[str] = []

    if not isinstance(manifest, dict):
        return ValidationResult(valid=False, errors=["Manifest must be a JSON object / dictionary."])

    version = manifest.get("version", "1.0")
    if not isinstance(version, str):
        errors.append("Field 'version' must be a string (e.g., '1.0').")

    if not manifest.get("name"):
        warnings.append("Manifest has no 'name' field; a default name will be assigned.")

    tasks = manifest.get("tasks")
    if not isinstance(tasks, list) or len(tasks) == 0:
        return ValidationResult(valid=False, errors=["Manifest must contain a non-empty 'tasks' list."])

    registry = workload_registry or WorkloadRegistry()
    seen_ids: Set[str] = set()
    graph: Dict[str, List[str]] = {}  # task_id -> list of dependencies
    in_degree: Dict[str, int] = {}

    for idx, t in enumerate(tasks):
        if not isinstance(t, dict):
            errors.append(f"Task at index {idx} must be an object.")
            continue

        tid = t.get("id")
        if not tid or not isinstance(tid, str):
            errors.append(f"Task at index {idx} is missing a valid string 'id'.")
            continue

        tid = str(tid).strip()
        if tid in seen_ids:
            errors.append(f"Duplicate task id '{tid}' detected.")
        seen_ids.add(tid)

        cmd = t.get("command")
        w_type = t.get("workload_type", "custom")

        if not cmd and w_type == "custom":
            errors.append(f"Task '{tid}' must specify a 'command' when workload_type is 'custom'.")
        elif not cmd and w_type != "custom":
            manifest_tmpl = registry.get(w_type)
            if not manifest_tmpl:
                errors.append(f"Task '{tid}' specifies unknown workload_type '{w_type}'.")
            elif manifest_tmpl.command_builder is None:
                errors.append(f"Workload template '{w_type}' has no command builder.")

        # Validate retry policies
        max_att = t.get("max_attempts", 1)
        if not isinstance(max_att, int) or max_att < 1:
            errors.append(f"Task '{tid}' max_attempts must be an integer >= 1.")

        # Validate requirements
        reqs = t.get("requirements", {})
        if isinstance(reqs, dict):
            min_vram = reqs.get("min_vram_gb", 0)
            if not isinstance(min_vram, (int, float)) or min_vram < 0:
                errors.append(f"Task '{tid}' requirements.min_vram_gb must be a non-negative number.")
        else:
            errors.append(f"Task '{tid}' requirements must be a dictionary.")

        deps = t.get("dependencies", [])
        if not isinstance(deps, list):
            errors.append(f"Task '{tid}' dependencies must be a list of task IDs.")
            deps = []

        graph[tid] = deps
        in_degree[tid] = len(deps)

    # Validate dependencies reference valid tasks and detect self-loops
    for tid, deps in graph.items():
        for d in deps:
            if d == tid:
                errors.append(f"Task '{tid}' cannot depend on itself.")
            elif d not in seen_ids:
                errors.append(f"Task '{tid}' references nonexistent dependency '{d}'.")

    # If syntax errors exist, stop here before cycle detection
    if errors:
        return ValidationResult(valid=False, errors=errors, warnings=warnings, task_count=len(tasks))

    # Cycle Detection and Parallel Tier Generation using Kahn's algorithm
    # Forward adjacency: dep -> dependent tasks
    dependents_map: Dict[str, List[str]] = {tid: [] for tid in seen_ids}
    degree_copy = dict(in_degree)
    for tid, deps in graph.items():
        for d in deps:
            dependents_map[d].append(tid)

    current_tier = [tid for tid, deg in degree_copy.items() if deg == 0]
    execution_order: List[List[str]] = []
    processed_count = 0

    while current_tier:
        execution_order.append(sorted(current_tier))
        processed_count += len(current_tier)
        next_tier = []
        for tid in current_tier:
            for child in dependents_map.get(tid, []):
                degree_copy[child] -= 1
                if degree_copy[child] == 0:
                    next_tier.append(child)
        current_tier = next_tier

    if processed_count < len(seen_ids):
        # A cycle exists among unvisited nodes
        cycle_nodes = [tid for tid, deg in degree_copy.items() if deg > 0]
        errors.append(f"Circular dependency cycle detected among tasks: {cycle_nodes}")
        return ValidationResult(valid=False, errors=errors, warnings=warnings, task_count=len(tasks))

    # Optional notifications check
    notifs = manifest.get("notifications")
    if notifs and isinstance(notifs, dict):
        url = notifs.get("webhook_url")
        if url and not (url.startswith("http://") or url.startswith("https://")):
            errors.append("Notification webhook_url must start with http:// or https://")

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        task_count=len(tasks),
        execution_order=execution_order,
    )


def evaluate_pod_for_task(
    pod_id: str,
    pod_status: Dict[str, Any],
    requirements: Dict[str, Any],
    active_tasks_on_pod: int = 0,
    allocated_vram_gb: float = 0.0,
) -> PodEvaluation:
    """
    Explainable resource-aware evaluation of a candidate pod against task requirements.
    Audits connectivity, occupancy, CUDA presence, and free VRAM headroom.
    """
    reasons: List[str] = []
    eligible = True
    score = 0.0

    # 1. Connectivity and health
    if "error" in pod_status or pod_status.get("status") == "UNREACHABLE":
        err_msg = pod_status.get("error", "pod unreachable")
        reasons.append(f"Rejected: Pod is unreachable or encountered error ({err_msg}).")
        return PodEvaluation(pod_id, eligible=False, reasons=reasons, score=0.0, telemetry=pod_status)

    # 2. Occupancy check
    if pod_status.get("is_occupied", False):
        eligible = False
        reasons.append("Rejected: Pod is currently occupied by active background services or training.")

    # 3. GPU and CUDA check
    req_gpu = bool(requirements.get("gpu", False) or requirements.get("min_vram_gb", 0) > 0)
    has_cuda = bool(pod_status.get("cuda_available", False))
    device_name = pod_status.get("device_name", "Unknown GPU")

    if req_gpu:
        if not has_cuda:
            eligible = False
            reasons.append("Rejected: Task requires CUDA GPU, but CUDA is unavailable on this pod.")
        else:
            reasons.append(f"Passed: CUDA GPU verified ({device_name}).")

    # 4. VRAM headroom matching
    min_vram = float(requirements.get("min_vram_gb", 0.0))
    raw_free = float(pod_status.get("free_vram_gb", 0.0))
    free_vram = max(0.0, raw_free - float(allocated_vram_gb))

    if min_vram > 0:
        if free_vram < min_vram:
            eligible = False
            reasons.append(
                f"Rejected: Insufficient free VRAM (requested {min_vram:.1f} GB, but pod has only {free_vram:.1f} GB free)."
            )
        else:
            reasons.append(
                f"Passed: Free VRAM satisfies requirement ({free_vram:.1f} GB free >= {min_vram:.1f} GB requested)."
            )

    # 5. Score computation for eligible pods
    if eligible:
        # Base score on free VRAM headroom
        score = free_vram
        if active_tasks_on_pod == 0:
            score += 100.0  # Reward idle pods with zero current tasks
            reasons.append("Bonus: Pod is idle with no active batch tasks assigned.")
        else:
            score -= (active_tasks_on_pod * 15.0)
            reasons.append(f"Note: Pod already has {active_tasks_on_pod} active task(s).")
        reasons.append(f"Selected as eligible with priority score {score:.1f}.")

    return PodEvaluation(
        pod_id=pod_id,
        eligible=eligible,
        reasons=reasons,
        score=score,
        telemetry=pod_status,
    )


class PodScheduler:
    """Dynamically matches batch tasks to available cloud pods without hardcoded IDs."""

    def __init__(self, client: Optional[MoLabClient] = None):
        self.client = client or MoLabClient()
        self._telemetry_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._cache_ttl = 5.0  # seconds

    def inspect_candidate_pods(self) -> Dict[str, Dict[str, Any]]:
        """Discover running cloud pods and inspect their live telemetry."""
        running_map = self.client.list_running_sandboxes()
        notebooks = {nb["id"]: nb for nb in self.client.list_notebooks()}

        now = time.time()
        pod_statuses: Dict[str, Dict[str, Any]] = {}

        for nb_id in running_map:
            # Check cache
            cached = self._telemetry_cache.get(nb_id)
            if cached and (now - cached[0]) < self._cache_ttl:
                pod_statuses[nb_id] = cached[1]
                continue

            nb_title = notebooks.get(nb_id, {}).get("title", "Untitled")
            try:
                session = SandboxSession(nb_id, client=self.client)
                status = session.get_workload_status()
                status["title"] = nb_title
                pod_statuses[nb_id] = status
                self._telemetry_cache[nb_id] = (now, status)
            except Exception as e:
                err_status = {
                    "notebook_id": nb_id,
                    "title": nb_title,
                    "error": str(e),
                    "is_occupied": True,
                    "status": "UNREACHABLE",
                }
                pod_statuses[nb_id] = err_status
                self._telemetry_cache[nb_id] = (now, err_status)

        return pod_statuses

    def select_pod_for_task(
        self,
        task: Dict[str, Any],
        candidate_pods: Dict[str, Dict[str, Any]],
        active_allocations: Dict[str, Any],
        vram_allocations: Optional[Dict[str, float]] = None,
    ) -> Tuple[Optional[str], List[PodEvaluation]]:
        """
        Evaluate candidate pods against task requirements and return the optimal pod.
        Returns (chosen_pod_id, list_of_all_evaluations).
        """
        reqs = task.get("requirements", {})
        assigned_pod = task.get("assigned_pod")
        evaluations: List[PodEvaluation] = []

        def _get_alloc_info(p_id: str) -> Tuple[int, float]:
            v_alloc = 0.0
            if vram_allocations and p_id in vram_allocations:
                v_alloc = float(vram_allocations[p_id])
            val = active_allocations.get(p_id, 0)
            if isinstance(val, float):
                v_alloc = max(v_alloc, val)
                t_count = 1 if val > 0 else 0
            else:
                t_count = int(val)
            return t_count, v_alloc

        # If task explicitly pins a specific pod
        if assigned_pod:
            if assigned_pod in candidate_pods:
                t_count, v_alloc = _get_alloc_info(assigned_pod)
                eval_res = evaluate_pod_for_task(
                    assigned_pod,
                    candidate_pods[assigned_pod],
                    reqs,
                    active_tasks_on_pod=t_count,
                    allocated_vram_gb=v_alloc,
                )
                evaluations.append(eval_res)
                if eval_res.eligible:
                    return assigned_pod, evaluations
                return None, evaluations
            else:
                evaluations.append(PodEvaluation(
                    pod_id=assigned_pod,
                    eligible=False,
                    reasons=[f"Pinned pod '{assigned_pod}' is not running or not found in workspace."],
                    score=0.0,
                ))
                return None, evaluations

        # Dynamic scheduling across all available running pods
        for pod_id, pod_status in candidate_pods.items():
            t_count, v_alloc = _get_alloc_info(pod_id)
            eval_res = evaluate_pod_for_task(
                pod_id,
                pod_status,
                reqs,
                active_tasks_on_pod=t_count,
                allocated_vram_gb=v_alloc,
            )
            evaluations.append(eval_res)

        eligible = [ev for ev in evaluations if ev.eligible]
        if not eligible:
            return None, evaluations

        # Sort eligible pods by score descending
        eligible.sort(key=lambda x: x.score, reverse=True)
        return eligible[0].pod_id, evaluations


class BatchOrchestrator:
    """
    Coordinates end-to-end multi-pod batch pipeline execution.
    Manages task lifecycles, dependencies, retry backoffs, and notifications.
    """

    def __init__(
        self,
        client: Optional[MoLabClient] = None,
        job_manager: Optional[JobManager] = None,
        workload_registry: Optional[WorkloadRegistry] = None,
    ):
        self.client = client or MoLabClient()
        self.job_manager = job_manager or JobManager()
        self.workload_registry = workload_registry or WorkloadRegistry()
        self.scheduler = PodScheduler(client=self.client)

    def validate(self, manifest: Dict[str, Any]) -> ValidationResult:
        """Validate batch manifest schema and dependency DAG."""
        return validate_manifest(manifest, workload_registry=self.workload_registry)

    def submit(
        self,
        manifest: Dict[str, Any],
        concurrency_limit: Optional[int] = None,
    ) -> str:
        """Validate and persist a batch pipeline in SQLite."""
        validation = self.validate(manifest)
        if not validation.valid:
            raise ValidationError(
                f"Batch manifest validation failed with {len(validation.errors)} error(s): "
                + "; ".join(validation.errors)
            )

        return self.job_manager.create_batch(manifest, concurrency_limit=concurrency_limit)

    def run_batch(
        self,
        batch_id: str,
        max_parallel: Optional[int] = None,
        poll_interval: float = 3.0,
        timeout: Optional[float] = None,
        on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """
        Execute an end-to-end multi-pod batch pipeline with live scheduling and monitoring.
        """
        batch = self.job_manager.get_batch(batch_id)
        manifest = batch.get("manifest", {})
        concurrency = max_parallel or batch.get("concurrency_limit", 4)

        # Setup notification dispatcher
        notif_cfg = manifest.get("notifications", {})
        dispatcher = NotificationDispatcher(
            webhook_url=notif_cfg.get("webhook_url"),
            events=notif_cfg.get("events"),
            job_manager=self.job_manager,
        )

        def emit(event_type: str, data: Dict[str, Any]) -> None:
            data["batch_id"] = batch_id
            dispatcher.dispatch(event_type, data, batch_id=batch_id)
            if on_event:
                try:
                    on_event(event_type, data)
                except Exception:
                    pass

        now = time.time()
        self.job_manager.update_batch_status(batch_id, status="RUNNING", started_at=now)
        emit("batch_started", {"batch_name": batch.get("name"), "task_count": len(batch.get("tasks", []))})

        worker_id = f"worker_{os.getpid()}_{uuid.uuid4().hex[:6]}"
        start_time = time.time()
        active_allocations: Dict[str, int] = {}

        # Reconcile previously RUNNING tasks after controller restarts
        initial_tasks = self.job_manager.get_batch_tasks(batch_id)
        for t in initial_tasks:
            if t["status"] == "RUNNING" and t.get("job_id"):
                try:
                    refreshed = self.job_manager.refresh_job_status(t["job_id"])
                    st = refreshed["status"]
                    if st == "COMPLETED":
                        self.job_manager.update_batch_task(
                            t["id"],
                            status="COMPLETED",
                            exit_code=0,
                            completed_at=time.time(),
                        )
                    elif st in ("FAILED", "LOST"):
                        is_idempotent = bool(t.get("idempotent", 1))
                        attempts = t.get("attempts", 1)
                        max_att = t.get("max_attempts", 1)
                        if is_idempotent and attempts < max_att:
                            self.job_manager.update_batch_task(
                                t["id"],
                                status="READY",
                                job_id=None,
                                assigned_pod=None if not t.get("metadata", {}).get("pinned_pod") else t.get("assigned_pod"),
                                error_message=f"Reconciled after controller restart: {refreshed.get('error_message')}. Retrying...",
                            )
                        else:
                            self.job_manager.update_batch_task(
                                t["id"],
                                status="FAILED",
                                exit_code=refreshed.get("exit_code", 1),
                                completed_at=time.time(),
                                error_message=f"Task lost or non-idempotent after restart: {refreshed.get('error_message')}",
                            )
                    elif st == "RUNNING":
                        assigned = t.get("assigned_pod")
                        if assigned:
                            active_allocations[assigned] = active_allocations.get(assigned, 0) + 1
                except Exception:
                    pass

        try:
            while True:
                # 1. Check batch overall timeout
                if timeout and (time.time() - start_time) > timeout:
                    self.job_manager.cancel_batch(batch_id)
                    self.job_manager.update_batch_status(
                        batch_id,
                        status="FAILED",
                        error_message=f"Batch execution timed out after {timeout} seconds.",
                        completed_at=time.time(),
                    )
                    emit("batch_failed", {"reason": f"Execution timed out ({timeout}s)"})
                    break

                # 2. Refresh active batch tasks
                tasks = self.job_manager.get_batch_tasks(batch_id)
                task_map = {t["task_key"]: t for t in tasks}

                # 3. Synchronize RUNNING tasks with remote pod state & check per-task timeouts
                running_tasks = [t for t in tasks if t["status"] == "RUNNING"]
                for t in running_tasks:
                    job_id = t.get("job_id")
                    if not job_id:
                        continue

                    # Per-task timeout enforcement
                    task_timeout = t.get("timeout_seconds") or t.get("metadata", {}).get("timeout_seconds")
                    started_at = t.get("started_at")
                    if task_timeout and started_at and (time.time() - started_at) > task_timeout:
                        try:
                            self.job_manager.cancel_job(job_id)
                        except Exception:
                            pass
                        self.job_manager.update_batch_task(
                            t["id"],
                            status="FAILED",
                            exit_code=124,
                            completed_at=time.time(),
                            error_message=f"Task execution exceeded timeout limit of {task_timeout} seconds.",
                        )
                        assigned = t.get("assigned_pod")
                        if assigned and assigned in active_allocations:
                            active_allocations[assigned] = max(0, active_allocations[assigned] - 1)
                        emit("task_failed", {
                            "task_key": t["task_key"],
                            "pod_id": assigned,
                            "error": f"Task timed out after {task_timeout}s",
                        })
                        continue

                    refreshed = self.job_manager.refresh_job_status(job_id)
                    st = refreshed["status"]

                    if st == "COMPLETED":
                        self.job_manager.update_batch_task(
                            t["id"],
                            status="COMPLETED",
                            exit_code=0,
                            completed_at=time.time(),
                        )
                        assigned = t.get("assigned_pod")
                        if assigned and assigned in active_allocations:
                            active_allocations[assigned] = max(0, active_allocations[assigned] - 1)
                        emit("task_completed", {"task_key": t["task_key"], "pod_id": assigned})

                    elif st in ("FAILED", "LOST"):
                        attempts = t.get("attempts", 1)
                        max_att = t.get("max_attempts", 1)
                        is_idempotent = bool(t.get("idempotent", 1))

                        if attempts < max_att and is_idempotent:
                            # Safe retry: unpin pod so scheduler can pick an alternate healthy pod
                            orig_pinned = t.get("metadata", {}).get("pinned_pod")
                            self.job_manager.update_batch_task(
                                t["id"],
                                status="READY",
                                job_id=None,
                                assigned_pod=orig_pinned if orig_pinned else None,
                                error_message=f"Attempt {attempts} failed: {refreshed.get('error_message')}. Retrying...",
                            )
                        else:
                            # Non-idempotent or exhausted retries
                            fail_msg = refreshed.get("error_message") or "Process failed."
                            if not is_idempotent and attempts < max_att:
                                fail_msg = f"Non-idempotent task failure: {fail_msg} (automatic rerun disabled)."
                            self.job_manager.update_batch_task(
                                t["id"],
                                status="FAILED",
                                exit_code=refreshed.get("exit_code", 1),
                                completed_at=time.time(),
                                error_message=fail_msg,
                            )
                        assigned = t.get("assigned_pod")
                        if assigned and assigned in active_allocations:
                            active_allocations[assigned] = max(0, active_allocations[assigned] - 1)
                        if attempts >= max_att or not is_idempotent:
                            emit("task_failed", {
                                "task_key": t["task_key"],
                                "pod_id": assigned,
                                "error": refreshed.get("error_message"),
                            })

                # Refresh tasks state after running synchronization
                tasks = self.job_manager.get_batch_tasks(batch_id)
                task_map = {t["task_key"]: t for t in tasks}

                # 4. Propagate dependency status to PENDING tasks
                pending_tasks = [t for t in tasks if t["status"] == "PENDING"]
                for t in pending_tasks:
                    deps = t.get("dependencies", [])
                    dep_statuses = [task_map.get(d, {}).get("status") for d in deps]

                    if any(s in ("FAILED", "SKIPPED", "CANCELLED") for s in dep_statuses):
                        # Prerequisite failed -> skip this task
                        self.job_manager.update_batch_task(
                            t["id"],
                            status="SKIPPED",
                            completed_at=time.time(),
                            error_message="Skipped because dependency task failed or was skipped.",
                        )
                    elif all(s == "COMPLETED" for s in dep_statuses):
                        # All prerequisites satisfied -> mark READY
                        self.job_manager.update_batch_task(t["id"], status="READY")

                # Refresh tasks again
                tasks = self.job_manager.get_batch_tasks(batch_id)
                ready_tasks = [t for t in tasks if t["status"] == "READY"]
                running_tasks = [t for t in tasks if t["status"] == "RUNNING"]

                # 5. Check completion criteria
                non_finished = [t for t in tasks if t["status"] in ("PENDING", "READY", "RUNNING")]
                if not non_finished:
                    failed_count = sum(1 for t in tasks if t["status"] == "FAILED")
                    final_status = "FAILED" if failed_count > 0 else "COMPLETED"
                    now = time.time()
                    self.job_manager.update_batch_status(
                        batch_id,
                        status=final_status,
                        completed_at=now,
                        error_message=f"{failed_count} task(s) failed." if failed_count > 0 else None,
                    )
                    emit(
                        f"batch_{final_status.lower()}",
                        {"failed_tasks": failed_count, "total_tasks": len(tasks)},
                    )
                    break

                # 6. Schedule READY tasks onto candidate pods up to concurrency limit
                if ready_tasks and len(running_tasks) < concurrency:
                    candidate_pods = self.scheduler.inspect_candidate_pods()
                    simulated_vram_alloc: Dict[str, float] = {}

                    for t in ready_tasks:
                        if len(running_tasks) >= concurrency:
                            break

                        # Recalculate pod capacity dynamically within this scheduling tick
                        adjusted_candidates = {}
                        for p_id, p_info in candidate_pods.items():
                            p_copy = dict(p_info)
                            current_free = float(p_copy.get("free_vram_gb", 0.0))
                            reserved = simulated_vram_alloc.get(p_id, 0.0)
                            p_copy["free_vram_gb"] = max(0.0, current_free - reserved)
                            adjusted_candidates[p_id] = p_copy

                        pod_id, evaluations = self.scheduler.select_pod_for_task(
                            t,
                            adjusted_candidates,
                            active_allocations,
                        )

                        if pod_id:
                            # Transactional claim lease prevents concurrent double-scheduling
                            if not self.job_manager.claim_batch_task(t["id"], worker_id=worker_id, lease_seconds=180.0):
                                continue

                            # Deduct required VRAM from this pod's capacity for the rest of this tick
                            task_vram = float(t.get("requirements", {}).get("min_vram_gb", 0.0))
                            simulated_vram_alloc[pod_id] = simulated_vram_alloc.get(pod_id, 0.0) + task_vram

                            # Launch task on selected pod
                            try:
                                cmd = t.get("command")
                                w_type = t.get("workload_type", "custom")
                                meta = t.get("metadata", {})
                                w_params = meta.get("workload_params", {})

                                if not cmd and w_type != "custom":
                                    tmpl = self.workload_registry.get(w_type)
                                    if tmpl and tmpl.command_builder:
                                        cmd = tmpl.command_builder(w_params)

                                if not cmd:
                                    raise SchedulingError(f"Task '{t['task_key']}' has no command to run.")

                                job = self.job_manager.submit_job(
                                    notebook_id=pod_id,
                                    command=cmd,
                                    name=f"{batch.get('name')}-{t['task_key']}",
                                    workload_type=w_type,
                                    workdir=t.get("workdir", "/workspace"),
                                    metadata={"batch_id": batch_id, "task_key": t["task_key"]},
                                )

                                attempts = t.get("attempts", 0) + 1
                                self.job_manager.update_batch_task(
                                    t["id"],
                                    status="RUNNING",
                                    assigned_pod=pod_id,
                                    job_id=job["id"],
                                    attempts=attempts,
                                    started_at=time.time(),
                                )
                                active_allocations[pod_id] = active_allocations.get(pod_id, 0) + 1
                                running_tasks.append(t)
                                emit("task_started", {
                                    "task_key": t["task_key"],
                                    "pod_id": pod_id,
                                    "job_id": job["id"],
                                })
                            except Exception as launch_err:
                                self.job_manager.update_batch_task(
                                    t["id"],
                                    status="FAILED",
                                    error_message=f"Failed to launch on pod {pod_id}: {launch_err}",
                                    completed_at=time.time(),
                                )
                                emit("task_failed", {
                                    "task_key": t["task_key"],
                                    "error": str(launch_err),
                                })

                time.sleep(poll_interval)

        except KeyboardInterrupt:
            self.job_manager.cancel_batch(batch_id)
            emit("batch_cancelled", {"reason": "Cancelled by user interrupt."})

        return self.job_manager.get_batch(batch_id)
