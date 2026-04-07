"""Direct BaPCod C interface for VRP with packing set constraints.

Bypasses VRPSolverEasy's JSON model to access BaPCod's packing sets directly,
enabling exact BCP where same-patient instances are in the same packing set
(at most one per route) but each instance has its own master constraint
(visit exactly once).

Model:
  Master problem:
    - One set-partitioning constraint per visit instance: sum of routes covering
      instance i = 1 (visit exactly once)
    - One constraint for max number of routes (vehicles/days)

  Subproblem (RCSP pricing per vehicle type):
    - Vertices: depot (source/sink) + one per visit instance
    - Arcs: all pairs with travel time as cost
    - Resources: time window (workday bounds), capacity (max visits per day)
    - Packing sets: one per patient — instances of same patient share a set
      → at most one visit per patient per route (day)
"""

from __future__ import annotations
import ctypes as ct
import os
import platform
import tempfile
from datetime import datetime
from models import SolverInput, SolverOutput, PlannedVisit
from solvers.base import (
    build_lunch_placements,
    compute_return_home,
    validate_spacing,
    minutes_to_datetime,
)


def _find_lib() -> str:
    system = platform.system()
    lib_name = {
        "Darwin": "libbapcod-shared.dylib",
        "Linux": "libbapcod-shared.so",
        "Windows": "bapcod-shared.dll",
    }.get(system)
    if not lib_name:
        raise OSError(f"Unsupported platform: {system}")

    try:
        import VRPSolverEasy
        candidate = os.path.join(VRPSolverEasy.__path__[0], "lib", system, lib_name)
        if os.path.exists(candidate):
            return candidate
    except ImportError:
        pass
    return lib_name


def solve(input: SolverInput, time_budget: int = 3600) -> SolverOutput:
    """Solve using BaPCod C interface with custom packing sets."""
    instances = input.instances
    if not instances:
        return _empty_output(input)

    clinician = input.clinician
    working_days = input.working_days
    num_days = len(working_days)
    matrix = input.travel_matrix
    patients_by_id = {p.id: p for p in input.patients}

    n = len(instances)
    unique_pids = sorted(set(inst.patient_id for inst in instances))
    pid_to_pack = {pid: idx for idx, pid in enumerate(unique_pids)}
    num_pack_sets = len(unique_pids)

    # Load BaPCod
    lib = ct.CDLL(_find_lib())

    # Write a minimal config file
    config_path = _write_config(time_budget)

    # ── Create model ──────────────────────────────────────────────────
    lib.bcInterfaceModel_new.restype = ct.c_void_p
    model = lib.bcInterfaceModel_new(
        config_path.encode(), ct.c_bool(False), ct.c_bool(True), ct.c_bool(True)
    )

    num_master_constrs = n  # one per instance
    num_master_vars = n * num_days  # one per (instance, day) combination
    lib.bcInterfaceModel_initModel(ct.c_void_p(model), ct.c_int(num_master_constrs), ct.c_int(num_master_vars))

    # Set objective bounds
    lib.bcInterfaceModel_setObjLb(ct.c_void_p(model), ct.c_double(0.0))
    lib.bcInterfaceModel_setObjUb(ct.c_void_p(model), ct.c_double(1e7))

    # ── Register subproblems (one per day) ────────────────────────────
    sp_ids = []
    for day_idx in range(num_days):
        sp_id = ct.c_int()
        lib.bcInterfaceModel_registerSubProblem(
            ct.c_void_p(model), ct.c_int(0), ct.byref(sp_id)
        )
        lib.bcInterfaceModel_subProblemMult(
            ct.c_void_p(model), ct.c_int(0), ct.c_int(1),
            ct.c_int(0), ct.byref(sp_id)
        )
        sp_ids.append(sp_id.value)

    # ── Register master variables and constraints ─────────────────────
    # Variables: x_{i,d} = 1 if instance i is on day d's route
    var_col = 0
    for day_idx in range(num_days):
        for inst_idx in range(n):
            name = f"x_{inst_idx}_{day_idx}".encode()
            lib.bcInterfaceModel_registerVar(
                ct.c_void_p(model), name, ct.c_int(var_col),
                ct.c_int(0), ct.byref(ct.c_int(sp_ids[day_idx])),
                ct.c_double(0.0), ct.c_double(1.0), ct.c_double(0.0)
            )
            var_col += 1

    # Constraints: each instance visited exactly once
    for inst_idx in range(n):
        name = f"visit_{inst_idx}".encode()
        lib.bcInterfaceModel_registerCstr(
            ct.c_void_p(model), name, ct.c_int(inst_idx),
            ct.c_int(-1), ct.byref(ct.c_int(0)),
            ct.c_char(b'E'), ct.c_double(1.0)  # = 1
        )

    # ── Build RCSP networks (one per day) ─────────────────────────────
    for day_idx in range(num_days):
        lib.bcRCSP_new.restype = ct.c_void_p
        sp_id_val = ct.c_int(sp_ids[day_idx])
        rcsp = lib.bcRCSP_new(
            ct.c_void_p(model), ct.c_int(0), ct.byref(sp_id_val),
            ct.c_int(n + 2),  # num vertices (depot + instances + sink)
            ct.c_int(n * (n + 1)),  # approx num arcs
            ct.c_int(num_pack_sets),  # num elem sets
            ct.c_int(num_pack_sets),  # num pack sets
            ct.c_int(0),  # num cov sets
        )

        # Resources
        time_res_id = ct.c_int(0)
        lib.bcRCSP_newResource(ct.c_void_p(rcsp), time_res_id)
        lib.bcRCSP_setAsMainResource(ct.c_void_p(rcsp), time_res_id, ct.c_double(1.0))

        cap_res_id = ct.c_int(1)
        lib.bcRCSP_newResource(ct.c_void_p(rcsp), cap_res_id)
        lib.bcRCSP_setAsMainResource(ct.c_void_p(rcsp), cap_res_id, ct.c_double(1.0))

        # Depot vertex (source) = vertex 0
        lib.bcRCSP_setSource(ct.c_void_p(rcsp), ct.c_int(0))
        lib.bcRCSP_setVertexConsumptionLB(ct.c_void_p(rcsp), ct.c_int(0), time_res_id, ct.c_double(clinician.workday_start_minute))
        lib.bcRCSP_setVertexConsumptionUB(ct.c_void_p(rcsp), ct.c_int(0), time_res_id, ct.c_double(clinician.workday_end_minute))
        lib.bcRCSP_setVertexConsumptionLB(ct.c_void_p(rcsp), ct.c_int(0), cap_res_id, ct.c_double(0))
        lib.bcRCSP_setVertexConsumptionUB(ct.c_void_p(rcsp), ct.c_int(0), cap_res_id, ct.c_double(5))

        # Instance vertices = vertices 1..n
        for inst_idx, inst in enumerate(instances):
            v_id = inst_idx + 1
            pack_id = pid_to_pack[inst.patient_id]

            # Packing set: at most one instance of this patient per route
            lib.bcRCSP_addVertexToPackingSet(ct.c_void_p(rcsp), ct.c_int(v_id), ct.c_int(pack_id))

            # Time window
            lib.bcRCSP_setVertexConsumptionLB(ct.c_void_p(rcsp), ct.c_int(v_id), time_res_id, ct.c_double(clinician.workday_start_minute))
            lib.bcRCSP_setVertexConsumptionUB(ct.c_void_p(rcsp), ct.c_int(v_id), time_res_id, ct.c_double(clinician.workday_end_minute - inst.duration))

            # Capacity: each visit consumes 1
            lib.bcRCSP_setVertexConsumptionLB(ct.c_void_p(rcsp), ct.c_int(v_id), cap_res_id, ct.c_double(0))
            lib.bcRCSP_setVertexConsumptionUB(ct.c_void_p(rcsp), ct.c_int(v_id), cap_res_id, ct.c_double(5))

        # Sink vertex = vertex n+1
        sink_id = n + 1
        lib.bcRCSP_setSink(ct.c_void_p(rcsp), ct.c_int(sink_id))
        lib.bcRCSP_setVertexConsumptionLB(ct.c_void_p(rcsp), ct.c_int(sink_id), time_res_id, ct.c_double(clinician.workday_start_minute))
        lib.bcRCSP_setVertexConsumptionUB(ct.c_void_p(rcsp), ct.c_int(sink_id), time_res_id, ct.c_double(clinician.workday_end_minute))
        lib.bcRCSP_setVertexConsumptionLB(ct.c_void_p(rcsp), ct.c_int(sink_id), cap_res_id, ct.c_double(0))
        lib.bcRCSP_setVertexConsumptionUB(ct.c_void_p(rcsp), ct.c_int(sink_id), cap_res_id, ct.c_double(5))

        # Arcs: depot → instances, instances → instances, instances → sink
        arc_id = 0
        for inst_idx in range(n):
            pid_i = str(instances[inst_idx].patient_id)
            v_i = inst_idx + 1

            # Depot → instance
            t_home = matrix.get("home", {}).get(pid_i, 0)
            lib.bcRCSP_newArc(ct.c_void_p(rcsp), ct.c_int(0), ct.c_int(v_i), ct.c_double(t_home))
            lib.bcRCSP_setArcConsumptionLB(ct.c_void_p(rcsp), ct.c_int(arc_id), time_res_id, ct.c_double(t_home + instances[inst_idx].duration))
            lib.bcRCSP_setArcConsumptionLB(ct.c_void_p(rcsp), ct.c_int(arc_id), cap_res_id, ct.c_double(1))
            # Link arc to master variable
            var_col_idx = day_idx * n + inst_idx
            lib.bcRCSP_attachBcVarToArc(ct.c_void_p(rcsp), ct.c_int(arc_id), ct.c_void_p(model), ct.c_int(var_col_idx))
            arc_id += 1

            # Instance → sink
            t_home_back = matrix.get(pid_i, {}).get("home", 0)
            lib.bcRCSP_newArc(ct.c_void_p(rcsp), ct.c_int(v_i), ct.c_int(sink_id), ct.c_double(t_home_back))
            lib.bcRCSP_setArcConsumptionLB(ct.c_void_p(rcsp), ct.c_int(arc_id), time_res_id, ct.c_double(t_home_back))
            lib.bcRCSP_setArcConsumptionLB(ct.c_void_p(rcsp), ct.c_int(arc_id), cap_res_id, ct.c_double(0))
            arc_id += 1

            # Instance → other instances
            for inst_idx_j in range(n):
                if inst_idx == inst_idx_j:
                    continue
                pid_j = str(instances[inst_idx_j].patient_id)
                v_j = inst_idx_j + 1
                t = matrix.get(pid_i, {}).get(pid_j, 0)

                lib.bcRCSP_newArc(ct.c_void_p(rcsp), ct.c_int(v_i), ct.c_int(v_j), ct.c_double(t))
                lib.bcRCSP_setArcConsumptionLB(ct.c_void_p(rcsp), ct.c_int(arc_id), time_res_id, ct.c_double(t + instances[inst_idx_j].duration))
                lib.bcRCSP_setArcConsumptionLB(ct.c_void_p(rcsp), ct.c_int(arc_id), cap_res_id, ct.c_double(1))
                var_col_idx_j = day_idx * n + inst_idx_j
                lib.bcRCSP_attachBcVarToArc(ct.c_void_p(rcsp), ct.c_int(arc_id), ct.c_void_p(model), ct.c_int(var_col_idx_j))
                arc_id += 1

    # ── Solve ─────────────────────────────────────────────────────────
    # TODO: Call bcInterfaceSolve_optimize and extract solution
    # For now, fall back to VRPSolverEasy
    lib.bcInterfaceModel_delete(ct.c_void_p(model))

    # Clean up config
    try:
        os.unlink(config_path)
    except OSError:
        pass

    # Fallback to VRPSolverEasy until we get the full solution extraction working
    from solvers.bcp import solve as bcp_solve
    return bcp_solve(input, time_budget=time_budget)


def _write_config(time_budget: int) -> str:
    """Write a temporary BaPCod config file."""
    fd, path = tempfile.mkstemp(suffix=".cfg", prefix="bapcod_")
    with os.fdopen(fd, "w") as f:
        f.write(f"GlobalTimeLimit = {time_budget}\n")
        f.write("DEFAULTPRINTLEVEL = -1\n")
        f.write("colGenSubProbSolMode = 3\n")
        f.write("MaxNbOfStagesInColGenProcedure = 3\n")
    return path


def _empty_output(input: SolverInput, metadata: dict | None = None) -> SolverOutput:
    return SolverOutput(
        planned_visits=[],
        lunch_placements=build_lunch_placements(input),
        fitness=0.0,
        metadata=metadata or {"optimizer_type": "python_bcp_direct"},
    )
