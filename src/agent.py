from langchain_core.messages import AnyMessage
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt.chat_agent_executor import AgentState
from langgraph.prebuilt import create_react_agent
from response_models import RepoAnalysis, GithubAgentState
from langgraph.graph import StateGraph, START, END
from typing import Dict, Any
from github_utils import add_comment_to_thread, commit_changes, create_new_branch, push_changes_and_create_pr
from langchain_core.tools import tool
import os
import subprocess

# Langfuse imports for tracing
from langfuse import Langfuse, get_client
from langfuse.langchain import CallbackHandler
import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions
Langfuse()

# Get the configured client instance
langfuse = get_client()

# Initialize the Langfuse handler
langfuse_handler = CallbackHandler()

async def coding_agent(state: GithubAgentState):
    options = ClaudeAgentOptions(
        system_prompt=f"""
        We have been given a repository and some review comments to address.
        An AI agent has analysed the validity of the review comment and decided that it is best to make changes as per the review comments.
        Please go through the original review comments and make the necessary changes as per the prompt.
        
        Given below is the original prompt to the previous AI agent:
        {state["messages"][0].content}

        Your final message should be a commit message for the changes you have made. Make it concise and to the point.
        """,
        permission_mode="bypassPermissions",
        max_turns=1000,
        cwd=f"/tmp/agent_repos/{state['repo']}",
        add_dirs=["/User/apanchangam/OmioCodebases/"]
    )
    final_response_from_claude = "Commit message from agent: "
    async for message in query(
        prompt=f"""
            Reasoning: {state["repo_analysis"].reasoning}
            Fix Prompt: {state["repo_analysis"].fix_prompt}
            Comment Reply: {state["repo_analysis"].comment_reply}
        """,
        options=options
    ):
        print(message)
        if hasattr(message, 'content'):
            final_response_from_claude = message.content
            print(f"response from claude agent: {message.content}")
    if isinstance(final_response_from_claude, list):
        final_response_from_claude = final_response_from_claude[0]
        if hasattr(final_response_from_claude, 'content'):
            final_response_from_claude = final_response_from_claude.content
        elif hasattr(final_response_from_claude, 'text'):
            final_response_from_claude = final_response_from_claude.text
        elif hasattr(final_response_from_claude, 'result'):
            final_response_from_claude = final_response_from_claude.response
        elif hasattr(final_response_from_claude, 'result'):
            final_response_from_claude = final_response_from_claude.result
        
    return final_response_from_claude


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

use the above tools to analyze the repository and review comment.
You will find the review comments in the user message.

<non_negotialbles>
Before making the decision, you must read the file that is mentioned in the review comment.
Ensure that the comment is still relevant to the file and it is not a stale comment.
The author of the comment can make mistakes and the comment might not be correct.
Ensure you provide the best possible analysis of the comment. Your decision will influcence the quality of the codebase over time.
</non_negotialbles>
"""
    return [{"role": "system", "content": system_msg}] + state["messages"]


def create_file_tools(repo_path: str):
    """Create file and directory reading tools for the specific repository"""
    
    @tool
    def read_file(file_path: str, start_line: int = 0, end_line: int = None) -> str:
        """
        Read the contents of a file from the repository with optional line range.
        
        Args:
            file_path: Path to the file relative to the repository root
            start_line: Starting line number (0-indexed, default: 0)
            end_line: Ending line number (0-indexed, default: None for entire file)
        
        Returns:
            String containing the file contents with line numbers prepended
        """
        try:
            full_path = os.path.join(repo_path, file_path)
            if not os.path.exists(full_path):
                return f"File not found: {file_path}"
            
            with open(full_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # Handle empty file case
            if len(lines) == 0:
                return ""
            
            # Handle line range with proper bounds checking
            if end_line is None:
                end_line = len(lines) - 1
            else:
                end_line = min(end_line, len(lines) - 1)
            
            # Ensure start_line is within bounds
            start_line = max(0, start_line)
            
            # Ensure start_line doesn't exceed end_line
            if start_line > end_line:
                return ""
            
            # Extract the requested lines
            selected_lines = lines[start_line:end_line + 1]
            
            # Add line numbers to each line
            result = ""
            for i, line in enumerate(selected_lines):
                line_number = start_line + i
                result += f"{line_number}->{line}"
            
            return result
        except Exception as e:
            return f"Error reading file {file_path}: {str(e)}"
    
    @tool
    def read_directory() -> str:
        """
        Display the directory structure using tree command, including hidden files.
        
        Args:
            directory_path: Path to the directory relative to the repository root (default: ".")
        
        Returns:
            String containing the tree structure of the directory
        """
        try:
            directory_path = "."
            full_path = os.path.join(repo_path, directory_path)
            if not os.path.exists(full_path):
                return f"Directory not found: {directory_path}"
            
            if not os.path.isdir(full_path):
                return f"Path is not a directory: {directory_path}"
            
            # Run tree command with -a flag to show hidden files
            result = subprocess.run(
                ["tree", "-a", directory_path], 
                cwd=repo_path,
                capture_output=True, 
                text=True, 
                timeout=30
            )
            
            if result.returncode == 0:
                return f"Directory structure for {directory_path}:\n{result.stdout}"
            else:
                # Fallback to ls if tree is not available
                ls_result = subprocess.run(
                    ["ls", "-la", directory_path], 
                    cwd=repo_path,
                    capture_output=True, 
                    text=True, 
                    timeout=10
                )
                if ls_result.returncode == 0:
                    return f"Directory listing for {directory_path}:\n{ls_result.stdout}"
                else:
                    return f"Error running tree/ls command: {result.stderr or ls_result.stderr}"
                    
        except subprocess.TimeoutExpired:
            return f"Timeout while reading directory {directory_path}"
        except Exception as e:
            return f"Error reading directory {directory_path}: {str(e)}"
    
    return [read_file, read_directory]

def invoke_comment_analysis(state: GithubAgentState) -> GithubAgentState:
    """Node 1: React agent analyzes the repository and comments"""
    # Create file tools for the specific repository
    repo_path = f"/tmp/agent_repos/{state['repo']}"
    file_tools = create_file_tools(repo_path)
    
    # Create agent with all tools
    dynamic_agent = create_react_agent(
        model="openai:gpt-5-mini-2025-08-07",
        tools=file_tools,
        prompt=prompt,
        response_format=RepoAnalysis
    )
    
    # Invoke agent with Langfuse tracing
    response = dynamic_agent.invoke(
        state, 
        config={"callbacks": [langfuse_handler]}
    )
    print(f"response: {response} {type(response)}")
    response['repo_analysis'] = response['structured_response']
    return response

def make_code_changes(state: GithubAgentState) -> GithubAgentState:
    """Node 2: Make necessary code changes (placeholder)"""
    # This node is intentionally left blank as requested
    print(f"make_code_changes: {state['messages'][-1].content}")
    coro = asyncio.gather(coding_agent(state))
    results = asyncio.get_event_loop().run_until_complete(coro)
    final_response_from_claude = results[0]
    print(f"final_response_from_claude: {final_response_from_claude}")
    create_new_branch(state['repo'], state['pr_number'], state['comment_node_id'])
    commit_changes(state['repo'], final_response_from_claude)
    res = push_changes_and_create_pr(state['repo'], state['pr_number'], new_branch_name=f"pr-{state['pr_number']}-response-{state['comment_node_id']}", pr_title="Agent fixes for PR", pr_body="Agent fixes for PR")
    print(res)
    return state

def post_comment_reply(state: GithubAgentState) -> GithubAgentState:
    """Node 3: Post comment reply to GitHub"""
    # This node will handle posting replies to GitHub comments
    print(f"post_comment_reply: {state['messages'][-1].content}")
    add_comment_to_thread(state["comment_node_id"], state["comment_id"], state["repo_analysis"].comment_reply)
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