"""FastAPI solver service — dispatches to HGS (PyVRP) or BCP (VRPSolverEasy)."""

from fastapi import FastAPI, HTTPException, Query
from models import SolverInput, SolverOutput
import solvers

app = FastAPI(title="RouteCare Solver Service", version="0.1.0")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/solve", response_model=SolverOutput)
def solve(
    input: SolverInput,
    backend: str = Query("cpsat", pattern="^(hgs|bcp|cpsat|pipeline)$"),
    time_budget: int = Query(30, ge=1, le=7200),
):
    """Solve a VRP instance.

    Backends:
      - cpsat: OR-Tools CP-SAT with full healthcare constraints (default)
      - hgs: PyVRP Hybrid Genetic Search (fast, near-optimal, travel-only)
      - bcp: VRPSolverEasy Branch-Cut-and-Price (slow, exact, travel-only)
      - pipeline: HGS first, then BCP with HGS upper bound
    """
    try:
        if backend == "cpsat":
            return solvers.cpsat_solve(input, time_budget=time_budget)

        elif backend == "hgs":
            return solvers.hgs_solve(input, time_budget=time_budget)

        elif backend == "bcp":
            return solvers.bcp_solve(input, time_budget=time_budget)

        elif backend == "pipeline":
            # Run HGS first for a good upper bound
            hgs_budget = min(time_budget // 4, 60)
            bcp_budget = time_budget - hgs_budget

            hgs_result = solvers.hgs_solve(input, time_budget=hgs_budget)

            # Try BCP with HGS upper bound; fall back to HGS if BCP unavailable
            try:
                bcp_result = solvers.bcp_solve(
                    input,
                    time_budget=bcp_budget,
                    upper_bound=hgs_result,
                )

                # Return BCP if it improved or proved optimal; otherwise HGS
                if bcp_result.metadata.get("proven_optimal"):
                    return bcp_result
                if bcp_result.fitness < hgs_result.fitness and bcp_result.planned_visits:
                    return bcp_result
            except Exception:
                pass  # BCP unavailable or failed — fall back to HGS

            return hgs_result

    except ImportError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
