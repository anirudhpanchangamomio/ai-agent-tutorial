from langchain_core.messages import AnyMessage
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt.chat_agent_executor import AgentState
from langgraph.prebuilt import create_react_agent
from response_models import RepoAnalysis, GithubAgentState
from langgraph.graph import StateGraph, START, END
from typing import Dict, Any
from github_tools import github_tools
from langchain_core.tools import tool
import os
import subprocess


def prompt(state: GithubAgentState) -> list[AnyMessage]:  
    
    system_msg = f"""You are a GitHub code review assistant. Your task is to analyze repository code and GitHub review comments to determine the appropriate action.

Given a repository and review comments, you should:
1. Analyze the code context around the comment
2. Understand the reviewer's feedback
3. Determine if a reply is needed or if code changes are required
4. Provide structured reasoning for your decision

Language rules:
- Prefer replying in the same language used by the latest review comment(s).
- If the review comment language cannot be determined, prefer the primary language used in the repository or code comments if available.
- If neither the comment language nor repository language can be determined, default to English.
- If the reviewer explicitly requests a response language (e.g., 'please reply in English'), follow their request.
- At the very start of your structured response include a single-line note indicating the language you are using, e.g.:\n  Language: English

Respond with a structured analysis including:
- action_type: "reply", "code_change", or "no_action"
- comment_reply: The reply text if action_type is "reply"
- fix_prompt: Instructions for code changes if action_type is "code_change"
- reasoning: Your analysis and reasoning for the decision

You are given the following tools to use:
- read_file: Read the contents of a file from the repository
- read_directory: Read the directory structure of the repository

Use the above tools to analyze the repository and review comment.
You will find the review comments in the user message.


"""
    return [{"role": "system", "content": system_msg}] + state["messages"]


def create_file_tools(repo_path: str):
    """Create file and directory reading tools for the specific repository"""
    
    @tool
    def read_file(file_path: str) -> str:
        """
        Read the contents of a file from the repository.
        
        Args:
            file_path: Path to the file relative to the repository root
        
        Returns:
            String containing the file contents
        """
        try:
            full_path = os.path.join(repo_path, file_path)
            if not os.path.exists(full_path):
                return f"File not found: {file_path}"
            
            with open(full_path, 'r', encoding='utf-8') as f:
                return f.read()
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
    
    # Combine GitHub tools with file tools
    file_tools
    
    # Create agent with all tools
    dynamic_agent = create_react_agent(
        model="openai:gpt-4o-mini",
        tools=file_tools,
        prompt=prompt,
        response_format=RepoAnalysis
    )
    
    return dynamic_agent.invoke(state)

def make_code_changes(state: GithubAgentState) -> GithubAgentState:
    """Node 2: Make necessary code changes (placeholder)"""
    # This node is intentionally left blank as requested
    print(f"make_code_changes: {state['messages'][-1].content}")
    return state

def post_comment_reply(state: GithubAgentState) -> GithubAgentState:
    """Node 3: Post comment reply to GitHub"""
    # This node will handle posting replies to GitHub comments
    print(f"post_comment_reply: {state['messages'][-1].content}")
    return state

def should_make_changes(state: GithubAgentState) -> str:
    """Conditional edge function to determine next action"""
    # Get the last message which should contain the RepoAnalysis
    last_message = state["messages"][-1]
    
    # Extract the structured response
    if hasattr(last_message, 'content') and isinstance(last_message.content, RepoAnalysis):
        analysis = last_message.content
        if analysis.action_type == "code_change":
            return "make_changes"
        elif analysis.action_type == "reply":
            return "post_reply"
        else:
            return "end"
    
    # Fallback to end if we can't parse the response
    return "end"

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