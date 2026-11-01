from typing import List, Optional
from pydantic import BaseModel, Field

class AgentDesignRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)
    objective: str = Field(..., min_length=8, max_length=2000)
    constraints: str = Field("", max_length=2000)
    tools: List[str] = []
    approval_actions: List[str] = []
    provider: str = "demo"
    model: str = ""

class AgentRunRequest(BaseModel):
    task: str = Field(..., min_length=1, max_length=10000)

class TestCaseRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    prompt: str = Field(..., min_length=1, max_length=10000)
    must_include: str = Field("", max_length=500)

class PromotionRequest(BaseModel):
    note: Optional[str] = Field("", max_length=1000)
