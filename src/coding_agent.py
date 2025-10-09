"""
Coding Agent module with support for both Claude and Aider backends.

This module provides a flexible CodingAgent class that can use either:
1. Claude Agent SDK for direct code execution
2. Aider Python scripting API for file-based code editing

Usage:
    # Using Claude backend
    agent = CodingAgent(backend="claude")
    result = await agent.execute_code_changes(state)
    
    # Using Aider backend
    agent = CodingAgent(backend="aider")
    result = await agent.execute_code_changes(state)
"""

import asyncio
import os
from typing import Dict, Any, Optional
from abc import ABC, abstractmethod

# Langfuse imports for tracing
from langfuse import get_client
from langfuse.langchain import CallbackHandler

# Claude imports
from claude_agent_sdk import ClaudeAgentOptions, query

# Aider imports
try:
    from aider.coders import Coder
    from aider.models import Model
    from aider.io import InputOutput
    AIDER_AVAILABLE = True
except ImportError:
    AIDER_AVAILABLE = False
    print("Warning: Aider not available. Install with: pip install aider-chat")


class CodingBackend(ABC):
    """Abstract base class for coding backends."""
    
    @abstractmethod
    async def execute_changes(self, state: Dict[str, Any]) -> str:
        """Execute code changes and return a commit message."""
        pass


class ClaudeBackend(CodingBackend):
    """Claude-based coding backend using Claude Agent SDK."""
    
    def __init__(self, additional_dirs: Optional[list] = None):
        self.additional_dirs = additional_dirs or []
    
    async def execute_changes(self, state: Dict[str, Any]) -> str:
        """Execute code changes using Claude Agent SDK."""
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
            add_dirs=self.additional_dirs
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
        
        # Clean up the response
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


class AiderBackend(CodingBackend):
    """Aider-based coding backend using Aider Python scripting API."""
    
    def __init__(self, model_name: str = "gpt-4-turbo", auto_accept: bool = True):
        if not AIDER_AVAILABLE:
            raise ImportError("Aider is not available. Install with: pip install aider-chat")
        
        self.model_name = model_name
        self.auto_accept = auto_accept
    
    async def execute_changes(self, state: Dict[str, Any]) -> str:
        """Execute code changes using Aider Python scripting API."""
        repo_path = f"/tmp/agent_repos/{state['repo']}"
        
        # Create IO handler with auto-accept if specified
        io = InputOutput(yes=self.auto_accept)
        
        # Create model
        model = Model(self.model_name)
        
        # Get all Python files in the repository
        python_files = self._get_python_files(repo_path)
        
        if not python_files:
            return "No Python files found to modify"
        
        # Create coder with all Python files
        coder = Coder.create(
            main_model=model, 
            fnames=python_files, 
            io=io
        )
        
        # Create the instruction for Aider
        instruction = f"""
        Based on the following analysis, make the necessary code changes:
        
        Reasoning: {state["repo_analysis"].reasoning}
        Fix Prompt: {state["repo_analysis"].fix_prompt}
        Comment Reply: {state["repo_analysis"].comment_reply}
        
        Original review comment: {state["messages"][0].content}
        
        Please implement the changes and provide a concise commit message.
        """
        
        # Execute the changes
        result = coder.run(instruction)
        
        # Extract commit message from the result
        commit_message = self._extract_commit_message(result)
        
        return commit_message or "Code changes implemented based on review comments"
    
    def _get_python_files(self, repo_path: str) -> list:
        """Get all Python files in the repository."""
        python_files = []
        for root, dirs, files in os.walk(repo_path):
            # Skip hidden directories and common non-source directories
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['__pycache__', 'node_modules', 'venv', 'env']]
            
            for file in files:
                if file.endswith('.py'):
                    rel_path = os.path.relpath(os.path.join(root, file), repo_path)
                    python_files.append(rel_path)
        
        return python_files[:10]  # Limit to first 10 files to avoid token limits
    
    def _extract_commit_message(self, result: str) -> str:
        """Extract commit message from Aider result."""
        # Simple extraction - look for common commit message patterns
        lines = result.split('\n')
        for line in lines:
            if any(keyword in line.lower() for keyword in ['commit', 'message', 'summary']):
                return line.strip()
        
        return "Code changes implemented"


class CodingAgent:
    """Main CodingAgent class that can use either Claude or Aider backend."""
    
    def __init__(self, backend: str = "claude", **kwargs):
        """
        Initialize the CodingAgent with the specified backend.
        
        Args:
            backend: Either "claude" or "aider"
            **kwargs: Additional arguments for the specific backend
        """
        self.backend_name = backend
        self.langfuse = get_client()
        
        if backend == "claude":
            self.backend = ClaudeBackend(
                additional_dirs=kwargs.get('additional_dirs', [])
            )
        elif backend == "aider":
            if not AIDER_AVAILABLE:
                raise ImportError("Aider is not available. Install with: pip install aider-chat")
            self.backend = AiderBackend(
                model_name=kwargs.get('model_name', 'gpt-4-turbo'),
                auto_accept=kwargs.get('auto_accept', True)
            )
        else:
            raise ValueError(f"Unsupported backend: {backend}. Use 'claude' or 'aider'")
    
    async def execute_code_changes(self, state: Dict[str, Any]) -> str:
        """
        Execute code changes using the configured backend with Langfuse tracing.
        
        Args:
            state: The GitHub agent state containing repository and analysis information
            
        Returns:
            Commit message for the changes made
        """
        # Create a trace for the coding agent
        with self.langfuse.start_as_current_span(
            name=f"coding_agent_{self.backend_name}",
            trace_id=state.get('main_agent_trace_id'),
            metadata={
                "agent_type": "coding_agent",
                "backend": self.backend_name,
                "repository": state['repo'],
                "pr_number": state.get('pr_number'),
                "comment_id": state.get('comment_id')
            }
        ) as span:
            # Update span with input
            span.update_trace(
                input={
                    "repository": state['repo'],
                    "reasoning": state["repo_analysis"].reasoning,
                    "fix_prompt": state["repo_analysis"].fix_prompt,
                    "comment_reply": state["repo_analysis"].comment_reply,
                    "backend": self.backend_name
                }
            )
            
            try:
                # Execute changes using the configured backend
                result = await self.backend.execute_changes(state)
                
                # Update span with output
                span.update_trace(
                    output={
                        "commit_message": result,
                        "backend_used": self.backend_name,
                        "success": True
                    }
                )
                
                return result
                
            except Exception as e:
                # Update span with error
                span.update_trace(
                    output={
                        "error": str(e),
                        "backend_used": self.backend_name,
                        "success": False
                    }
                )
                raise


# Convenience function for backward compatibility
async def coding_agent(state: Dict[str, Any], backend: str = "claude", **kwargs) -> str:
    """
    Convenience function for backward compatibility.
    
    Args:
        state: The GitHub agent state
        backend: Either "claude" or "aider"
        **kwargs: Additional arguments for the specific backend
        
    Returns:
        Commit message for the changes made
    """
    agent = CodingAgent(backend=backend, **kwargs)
    return await agent.execute_code_changes(state)
