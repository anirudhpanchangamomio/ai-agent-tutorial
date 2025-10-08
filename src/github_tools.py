from langchain_core.tools import tool
from typing import Dict, List, Any
import os
import subprocess
import json
from github_utils import build_review_comment_dict, get_github_pr_diff


@tool
def get_repository_comments(owner: str, repo: str, pr_limit: int = 5) -> str:
    """
    Get review comments from open pull requests in a GitHub repository.
    
    Args:
        owner: Repository owner/org (e.g., 'microsoft')
        repo: Repository name (e.g., 'vscode')
        pr_limit: Maximum number of PRs to analyze (default: 5)
    
    Returns:
        JSON string containing review comment threads
    """
    try:
        comments_data = build_review_comment_dict(owner, repo, pr_limit=pr_limit)
        return json.dumps(comments_data, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Error fetching comments: {str(e)}"


@tool
def get_pr_diff(owner: str, repo: str, pr_number: int) -> str:
    """
    Get the diff for a specific pull request.
    
    Args:
        owner: Repository owner/org
        repo: Repository name
        pr_number: Pull request number
    
    Returns:
        String containing the PR diff
    """
    try:
        # Clone repo and checkout PR
        original_cwd = os.getcwd()
        os.chdir(f"/tmp/agent_repos")
        
        # Clone if not exists
        if not os.path.exists(repo):
            subprocess.run(["gh", "repo", "clone", f"{owner}/{repo}"], 
                         capture_output=True, text=True, check=True)
        
        os.chdir(f"/tmp/agent_repos/{repo}")
        
        # Checkout PR
        subprocess.run(["gh", "pr", "checkout", pr_number], 
                      capture_output=True, text=True, check=True)
        
        # Get diff
        result = subprocess.run(["gh", "pr", "diff", pr_number], 
                              capture_output=True, text=True, check=True)
        
        os.chdir(original_cwd)
        return result.stdout
    except Exception as e:
        return f"Error getting PR diff: {str(e)}"


@tool
def analyze_file_content(file_path: str, start_line: int, end_line: int = None) -> str:
    """
    Analyze the content of a specific file around a given line range.
    
    Args:
        file_path: Path to the file relative to repository root
        start_line: Starting line number
        end_line: Ending line number (optional, defaults to start_line + 10)
    
    Returns:
        String containing the file content around the specified lines
    """
    try:
        if end_line is None:
            end_line = start_line + 10
            
        # Read file from the checked out repository
        repo_path = "/tmp/agent_repos"
        full_path = os.path.join(repo_path, file_path)
        
        if not os.path.exists(full_path):
            return f"File not found: {file_path}"
            
        with open(full_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        # Extract lines around the specified range
        start_idx = max(0, start_line - 5)
        end_idx = min(len(lines), end_line + 5)
        
        context_lines = lines[start_idx:end_idx]
        line_numbers = range(start_idx + 1, end_idx + 1)
        
        result = f"File: {file_path}\nLines {start_idx + 1}-{end_idx}:\n"
        for line_num, line in zip(line_numbers, context_lines):
            result += f"{line_num:4d}: {line}"
            
        return result
    except Exception as e:
        return f"Error reading file: {str(e)}"


@tool
def get_comment_context(comment_data: str) -> str:
    """
    Extract and format comment context information.
    
    Args:
        comment_data: JSON string containing comment information
    
    Returns:
        Formatted string with comment context
    """
    try:
        comment = json.loads(comment_data)
        
        context = f"""
Comment Context:
- Author: {comment.get('author', 'Unknown')}
- Created: {comment.get('createdAt', 'Unknown')}
- File: {comment.get('path', 'Unknown')}
- Line: {comment.get('line', 'Unknown')}
- Comment: {comment.get('body', 'No content')}
- PR Number: {comment.get('pullRequestNumber', 'Unknown')}
"""
        return context
    except Exception as e:
        return f"Error parsing comment: {str(e)}"


# List of tools to be used by the agent
github_tools = [
    get_repository_comments,
    get_pr_diff,
    analyze_file_content,
    get_comment_context
]
