from langchain_core.messages import AnyMessage
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt.chat_agent_executor import AgentState
from langgraph.prebuilt import create_react_agent
from response_models import RepoAnalysis, GithubAgentState
from langgraph.graph import StateGraph, START, END
from typing import Dict, Any
from github_utils import add_comment_to_thread, commit_changes, create_new_branch, push_changes_and_create_pr
from github_tools import github_tools
from tools import create_file_tools
from sub_agents import create_traced_subagent_tool
from coding_agent import CodingAgent
import os
import subprocess
import asyncio
from claude_agent_sdk import ClaudeAgentOptions, query

# Langfuse imports for tracing
from langfuse import Langfuse, get_client
from langfuse.langchain import CallbackHandler

# Initialize Langfuse client (uses environment variables)
# Set these environment variables:
# LANGFUSE_PUBLIC_KEY="your-public-key"
# LANGFUSE_SECRET_KEY="your-secret-key"
# LANGFUSE_HOST="https://cloud.langfuse.com"  # Optional: defaults to https://cloud.langfuse.com
Langfuse()

# Get the configured client instance
langfuse = get_client()

# Initialize the Langfuse handler
langfuse_handler = CallbackHandler()



def prompt(state: GithubAgentState) -> list[AnyMessage]:  
    
    system_msg = f"""You are a GitHub code review assistant. Your task is to analyze repository code and GitHub review comments to determine the appropriate action.

Given a repository and review comments, you should:
1. Analyze the code context around the comment
2. Understand the reviewer's feedback
3. Determine if a reply is needed or if code changes are required
4. Provide structured reasoning for your decision

Respond with a structured analysis including:
- action_type: "reply", "code_change", or "no_action"
- comment_reply: The reply text if action_type is "reply"
- fix_prompt: Instructions for code changes if action_type is "code_change"
- reasoning: Your analysis and reasoning for the decision

You are given the following tools to use:
- read_file: Read the contents of a file from the repository
- read_directory: Read the directory structure of the repository
- grep: Search the repository for a specific string
- sub_agent: Analyze a code repository that is not the one that is being reviewed

Use the above tools to analyze the repository and review comment.
You will find the review comments in the user message.

<non_negotialbles>
Before making the decision, you must read the file that is mentioned in the review comment.
Ensure that the comment is still relevant to the file and it is not a stale comment.
The author of the comment can make mistakes and the comment might not be correct.
Ensure you provide the best possible analysis of the comment. Your decision will influcence the quality of the codebase over time.
</non_negotialbles>
"""
    return [{"role": "system", "content": system_msg}] + state["messages"]


def invoke_comment_analysis(state: GithubAgentState) -> GithubAgentState:
    """Node 1: React agent analyzes the repository and comments"""
    # Create a main agent trace
    with langfuse.start_as_current_span(
        name=f"comment-analysis-{state['repo']}-{state['pr_number']}",
        metadata={
            "agent_type": "comment_analysis",
            "repository": state['repo'],
            "pr_number": state.get('pr_number'),
            "comment_id": state.get('comment_id'),
            "main_agent_trace_id": state.get('main_agent_trace_id')
        }
    ) as main_span:
        # Get the current trace ID to pass to subagents
        current_trace_id = main_span.trace_id
        
        # Create file tools for the specific repository
        repo_path = f"/tmp/agent_repos/{state['repo']}"
        file_tools = create_file_tools(repo_path)
        sub_agent = create_traced_subagent_tool(current_trace_id)
        
        # Combine file tools with the traced subagent tool
        all_tools = file_tools + [sub_agent]
        
        # Create agent with all tools
        dynamic_agent = create_react_agent(
            model="openai:gpt-5-mini-2025-08-07",
            tools=all_tools,
            prompt=prompt,
            response_format=RepoAnalysis
        )
        
        # Update main span with input
        main_span.update_trace(
            input={
                "repository": state['repo'],
                "messages": [msg.content for msg in state['messages']],
                "trace_id": current_trace_id
            }
        )
        
        # Invoke agent with Langfuse tracing
        response = dynamic_agent.invoke(
            state, 
            config={
                "callbacks": [langfuse_handler],
                "metadata": {
                    "langfuse_user_id": f"main_agent_{state['repo']}",
                    "main_trace_id": current_trace_id
                }
            }
        )
        
        print(f"response: {response} {type(response)}")
        response['repo_analysis'] = response['structured_response']
        
        # Update main span with output
        main_span.update_trace(
            output={
                "analysis_result": response['repo_analysis'],
                "trace_id": current_trace_id
            }
        )
        
        # Store the trace ID in the state for subagents to use
        response['main_agent_trace_id'] = current_trace_id
        
        return response

def make_code_changes(state: GithubAgentState) -> GithubAgentState:
    """Node 2: Make necessary code changes (placeholder)"""
    # Create a trace for code changes
    with langfuse.start_as_current_span(
        name=f"code-changes-{state['repo']}-{state['pr_number']}",
        trace_id=state.get('main_agent_trace_id'),  # Link to main agent trace
        metadata={
            "agent_type": "main_agent",
            "action": "code_changes",
            "repository": state['repo'],
            "pr_number": state.get('pr_number'),
            "comment_id": state.get('comment_id')
        }
    ) as code_span:
        print(f"make_code_changes: {state['messages'][-1].content}")
        
        # Update span with input
        code_span.update_trace(
            input={
                "repository": state['repo'],
                "pr_number": state.get('pr_number'),
                "comment_node_id": state.get('comment_node_id'),
                "main_trace_id": state.get('main_agent_trace_id')
            }
        )
        
        # Initialize coding agent (can use either "claude" or "aider" backend)
        coding_agent = CodingAgent(backend="claude", additional_dirs=["/User/apanchangam/OmioCodebases/"])
        
        # Execute code changes
        coro = asyncio.gather(coding_agent.execute_code_changes(state))
        results = asyncio.get_event_loop().run_until_complete(coro)
        final_response_from_claude = results[0]
        print(f"final_response_from_claude: {final_response_from_claude}")
        
        create_new_branch(state['repo'], state['pr_number'], state['comment_node_id'])
        commit_changes(state['repo'], final_response_from_claude)
        res = push_changes_and_create_pr(state['repo'], state['pr_number'], new_branch_name=f"pr-{state['pr_number']}-response-{state['comment_node_id']}", pr_title="Agent fixes for PR", pr_body="Agent fixes for PR")
        if state['repo_analysis'].comment_reply is not None:
            add_comment_to_thread(state['comment_node_id'], state['comment_id'], state['repo_analysis'].comment_reply)
        print(res)
        
        # Update span with output
        code_span.update_trace(
            output={
                "commit_message": final_response_from_claude,
                "pr_created": res,
                "branch_name": f"pr-{state['pr_number']}-response-{state['comment_node_id']}"
            }
        )
        
        return state

def post_comment_reply(state: GithubAgentState) -> GithubAgentState:
    """Node 3: Post comment reply to GitHub"""
    # Create a trace for comment reply
    with langfuse.start_as_current_span(
        name="main_agent_comment_reply",
        trace_id=state.get('main_agent_trace_id'),  # Link to main agent trace
        metadata={
            "agent_type": "main_agent",
            "action": "comment_reply",
            "repository": state['repo'],
            "pr_number": state.get('pr_number'),
            "comment_id": state.get('comment_id')
        }
    ) as reply_span:
        print(f"post_comment_reply: {state['messages'][-1].content}")
        
        # Update span with input
        reply_span.update_trace(
            input={
                "repository": state['repo'],
                "comment_node_id": state.get('comment_node_id'),
                "comment_id": state.get('comment_id'),
                "reply_text": state["repo_analysis"].comment_reply,
                "main_trace_id": state.get('main_agent_trace_id')
            }
        )
        
        add_comment_to_thread(state["comment_node_id"], state["comment_id"], state["repo_analysis"].comment_reply)
        
        # Update span with output
        reply_span.update_trace(
            output={
                "comment_posted": True,
                "reply_text": state["repo_analysis"].comment_reply
            }
        )
        
        return state

def should_make_changes(state: GithubAgentState) -> str:
    """Conditional edge function to determine next action"""
    # Get the last message which should contain the RepoAnalysis
    last_message = state["messages"][-1]
    print(f"last message: {last_message.content}")
    print(f"last message type: {type(last_message.content)}")
    # Extract the structured response
    print(f"state repo_analysis: {state['repo_analysis']}")
    if hasattr(state['repo_analysis'], 'action_type'):
        analysis = state['repo_analysis'].action_type
        if analysis == "code_change":
            return "make_changes"
        elif analysis == "reply":
            return "post_reply"
        else:
            return END
    
    # Fallback to end if we can't parse the response
    return END

# Build the graph
graph_builder = StateGraph(GithubAgentState)

# Add nodes
graph_builder.add_node("analyze_comments", invoke_comment_analysis)
graph_builder.add_node("make_changes", make_code_changes)
graph_builder.add_node("post_reply", post_comment_reply)

# Add edges
graph_builder.add_edge(START, "analyze_comments")
graph_builder.add_conditional_edges(
    "analyze_comments",
    should_make_changes,
    {
        "make_changes": "make_changes",
        "post_reply": "post_reply", 
        "end": END
    }
)
graph_builder.add_edge("make_changes", END)
graph_builder.add_edge("post_reply", END)

# Compile the graph
graph = graph_builder.compile()