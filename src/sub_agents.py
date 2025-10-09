"""
Subagents module with Langfuse tracing integration.

This module provides subagent functionality with comprehensive tracing using Langfuse.
Each subagent call can be linked to a main agent trace for better observability.

Usage:
    # From main agent, create a traced subagent tool
    current_trace_id = main_span.trace_id
    traced_subagent_tool = create_traced_subagent_tool(current_trace_id)
    
    # Add the tool to your agent's tools list
    all_tools = file_tools + [traced_subagent_tool]
    
    # The LLM can now call the subagent without needing to pass trace IDs
    # The trace linking is handled automatically by the wrapper function

Environment Variables Required:
    LANGFUSE_PUBLIC_KEY: Your Langfuse public key
    LANGFUSE_SECRET_KEY: Your Langfuse secret key
    LANGFUSE_HOST: Langfuse host (optional, defaults to https://cloud.langfuse.com)
"""

from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from tools import create_file_tools
from github_utils import clone_repo
from langfuse import get_client
from langfuse.langchain import CallbackHandler


def analyse_relavent_repo(repository_name: str, prompt: str, main_agent_trace_id: str = None):
    """
    Use this tool to analyse a code repository that is not the one that is being reviewed.
    Only the final message from this agent will be presented as the response to the LLM.
    
    Args:
        repository_name: Name of the repository to analyze
        prompt: The prompt/question for the analysis
        main_agent_trace_id: Optional trace ID from the main agent to link this subagent call
    """
    # Get Langfuse client
    langfuse = get_client()
    
    # Create a subagent trace that links to the main agent trace
    with langfuse.start_as_current_span(
        name=f"subagent_analysis_{repository_name}",
        trace_id=main_agent_trace_id,  # Link to main agent trace if provided
        metadata={
            "subagent_type": "repository_analysis",
            "repository_name": repository_name,
            "main_agent_trace_id": main_agent_trace_id
        }
    ) as span:
        system_prompt = f"""
        You are a GitHub code review assistant. Your task is to analyze repository code based on the prompt given to you.
        
        You have the following tools to use:
        - read_file: Read the contents of a file from the repository
        - read_directory: Read the directory structure of the repository
        - grep: Search the repository for a specific string
        - 

        <expected-response>
        Only the final message from this agent will be presented as the response to the LLM. So ensure that everything that needs to be conveyed is in the final message.
        </expected-response>
        """

        repo_path = f"/tmp/agent_repos/{repository_name}"
        clone_repo(repository_name, repo_path)
        file_tools = create_file_tools(repository_name)
        prompts = [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}]
        
        # Create Langfuse handler for the subagent
        langfuse_handler = CallbackHandler()
        
        react_sub_agent = create_react_agent(
            model="openai:gpt-5-mini-2025-08-07",
            tools=file_tools,
            prompt=prompts
        )
        
        # Update span with input information
        span.update_trace(
            input={
                "repository_name": repository_name,
                "prompt": prompt,
                "main_agent_trace_id": main_agent_trace_id
            }
        )
        
        # Invoke the subagent with Langfuse tracing
        response = react_sub_agent.invoke(
            prompt=prompts,
            config={"callbacks": [langfuse_handler]}
        )
        
        result = response['messages'][-1].content
        
        # Update span with output information
        span.update_trace(
            output={
                "analysis_result": result,
                "repository_analyzed": repository_name
            }
        )
        
        return result


def create_traced_subagent_tool(main_agent_trace_id: str):
    """
    Creates a wrapper function for the subagent that automatically includes trace identification.
    This function should be called from the main agent to get a tool that automatically
    links subagent calls to the main agent trace.
    
    Args:
        main_agent_trace_id: The trace ID from the main agent to link subagent calls
    
    Returns:
        A tool function that can be used by the LLM without needing to pass trace IDs
    """
    from langchain_core.tools import tool
    
    @tool
    def analyse_relevant_repo_traced(repository_name: str, prompt: str):
        """
        Use this tool to analyse a code repository that is not the one that is being reviewed.
        Only the final message from this agent will be presented as the response to the LLM.
        This tool automatically links to the main agent trace for observability.
        """
        return analyse_relavent_repo(
            repository_name=repository_name,
            prompt=prompt,
            main_agent_trace_id=main_agent_trace_id
        )
    
    return analyse_relevant_repo_traced


def create_subagent_with_trace(repository_name: str, prompt: str, main_agent_trace_id: str = None):
    """
    Helper function to create a subagent with proper trace identification.
    This function demonstrates how to call the subagent with trace linking.
    
    Args:
        repository_name: Name of the repository to analyze
        prompt: The prompt/question for the analysis
        main_agent_trace_id: Trace ID from the main agent to link this subagent call
    
    Returns:
        Analysis result from the subagent
    """
    return analyse_relavent_repo(
        repository_name=repository_name,
        prompt=prompt,
        main_agent_trace_id=main_agent_trace_id
    )