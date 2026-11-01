from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from . import factory, registry, runtime
from .models import AgentDesignRequest, AgentRunRequest, TestCaseRequest, PromotionRequest

ROOT = Path(__file__).resolve().parents[1]
registry.init_db()
app = FastAPI(title="Local Agent Factory", version="0.2.0", description="Schema-first local control plane for governed AI agents.")
app.mount("/static", StaticFiles(directory=ROOT), name="static")

@app.get("/")
def dashboard(): return FileResponse(ROOT / "index.html")
@app.get("/health")
def health(): return {"status":"healthy","factory":"local","storage":str(registry.DB_PATH)}
@app.get("/api/catalog")
def catalog(): return {"tools":factory.ALLOWED_TOOLS,"approval_actions":sorted(factory.ALLOWED_APPROVALS)}
@app.get("/api/agents")
def agents(): return registry.list_agents()
@app.post("/api/agents", status_code=201)
def design_agent(request: AgentDesignRequest): return registry.create_agent(request.name,request.objective,request.provider,request.model,factory.build_spec(request))
@app.get("/api/agents/{agent_id}")
def agent(agent_id:int):
    result=registry.get_agent(agent_id)
    if not result: raise HTTPException(404,"Agent not found")
    result["versions"]=registry.list_versions(agent_id); return result
@app.get("/api/versions/{version_id}")
def version(version_id:int):
    result=registry.get_version(version_id)
    if not result: raise HTTPException(404,"Version not found")
    result["tests"]=registry.list_tests(version_id); return result
@app.post("/api/versions/{version_id}/run", status_code=201)
def run(version_id:int,request:AgentRunRequest):
    current=registry.get_version(version_id)
    if not current: raise HTTPException(404,"Version not found")
    output,status,duration=runtime.execute(current,request.task); return registry.save_run(version_id,request.task,output,status,duration)
@app.post("/api/versions/{version_id}/tests",status_code=201)
def create_test(version_id:int,request:TestCaseRequest):
    if not registry.get_version(version_id): raise HTTPException(404,"Version not found")
    return registry.add_test(version_id,request.name,request.prompt,request.must_include)
@app.post("/api/versions/{version_id}/evaluate")
def evaluate_version(version_id:int):
    current=registry.get_version(version_id)
    if not current: raise HTTPException(404,"Version not found")
    tests=registry.list_tests(version_id)
    if not tests: raise HTTPException(400,"Add at least one test before evaluation.")
    results=[]
    for test in tests:
        output,status,duration=runtime.execute(current,test["prompt"]); saved=registry.save_run(version_id,test["prompt"],output,status,duration); passed,score,rationale=runtime.evaluate(output,test); registry.save_evaluation(test["id"],saved["id"],passed,score,rationale); results.append({"test":test["name"],"passed":passed,"score":score,"rationale":rationale,"output":output})
    return {"version":current["version"],"passed":all(item["passed"] for item in results),"results":results}
@app.post("/api/versions/{version_id}/promote")
def promote(version_id:int,request:PromotionRequest):
    result=registry.promote(version_id,request.note)
    if not result: raise HTTPException(404,"Version not found")
    return result
@app.get("/api/runs")
def runs(): return registry.recent_runs()
