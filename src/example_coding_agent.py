"""
Example usage of the CodingAgent class with both Claude and Aider backends.

This script demonstrates how to use the CodingAgent class for code changes
with different backends and configurations.
"""

import asyncio
from coding_agent import CodingAgent


async def example_claude_backend():
    """Example using Claude backend."""
    print("=== Claude Backend Example ===")
    
    # Mock state for demonstration
    state = {
        "repo": "example-repo",
        "pr_number": "123",
        "comment_id": "456",
        "main_agent_trace_id": "trace-123",
        "messages": [{"content": "Please fix the bug in the authentication module"}],
        "repo_analysis": type('obj', (object,), {
            'reasoning': 'The authentication module has a security vulnerability',
            'fix_prompt': 'Update the authentication logic to use secure password hashing',
            'comment_reply': 'I will fix the authentication security issue'
        })
    }
    
    # Initialize Claude backend
    agent = CodingAgent(
        backend="claude",
        additional_dirs=["/path/to/your/codebase"]
    )
    
    try:
        result = await agent.execute_code_changes(state)
        print(f"Claude result: {result}")
    except Exception as e:
        print(f"Claude error: {e}")


async def example_aider_backend():
    """Example using Aider backend."""
    print("\n=== Aider Backend Example ===")
    
    # Mock state for demonstration
    state = {
        "repo": "example-repo",
        "pr_number": "123", 
        "comment_id": "456",
        "main_agent_trace_id": "trace-123",
        "messages": [{"content": "Please add error handling to the API endpoints"}],
        "repo_analysis": type('obj', (object,), {
            'reasoning': 'API endpoints lack proper error handling',
            'fix_prompt': 'Add try-catch blocks and proper error responses',
            'comment_reply': 'I will add comprehensive error handling'
        })
    }
    
    # Initialize Aider backend
    agent = CodingAgent(
        backend="aider",
        model_name="gpt-4-turbo",
        auto_accept=True
    )
    
    try:
        result = await agent.execute_code_changes(state)
        print(f"Aider result: {result}")
    except Exception as e:
        print(f"Aider error: {e}")


async def main():
    """Run both examples."""
    await example_claude_backend()
    await example_aider_backend()


if __name__ == "__main__":
    asyncio.run(main())
