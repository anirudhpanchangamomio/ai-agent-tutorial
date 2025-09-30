from pydantic import BaseModel
from typing import Literal, Annotated
from langgraph.prebuilt.chat_agent_executor import AgentState

class RepoAnalysis(BaseModel):
    action_type: Literal["reply", "code_change", "no_action"]
    comment_reply: str
    fix_prompt: str
    reasoning: str

class GithubAgentState(AgentState):
    repo: str
    repo_analysis: Annotated[RepoAnalysis, "The analysis of the repository"]
    pr_number: int
    comment_node_id: str