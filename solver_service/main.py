"""FastAPI solver service — VRPTW healthcare scheduling optimizer."""

from fastapi import FastAPI, HTTPException, Query
from models import SolverInput, SolverOutput, SolveRequest
import solver

app = FastAPI(title="RouteCare Solver Service", version="1.0.0")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/solve", response_model=SolverOutput)
def solve(
    body: SolveRequest,
    time_budget: int = Query(30, ge=1, le=7200),
):
    """Solve a healthcare scheduling instance using the VRPTW optimizer.

    Body may include optional ``upper_bound`` (full SolverOutput) for warm-start
    when instances match.
    """
    try:
        input_payload = body.model_dump(exclude={"upper_bound"})
        input = SolverInput.model_validate(input_payload)
        return solver.solve(
            input, time_budget=time_budget, upper_bound=body.upper_bound
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
