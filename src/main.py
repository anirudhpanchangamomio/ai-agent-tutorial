from dotenv import load_dotenv
load_dotenv()
from github_utils import clone_repo, checkout_pr, build_review_comment_dict, create_new_branch, commit_changes, clear_directory, get_github_pr_diff, ensure_directory_exists
from argparse import ArgumentParser
import sys
from agent import graph
from langchain_core.messages import HumanMessage


def main(owner, repo):
    """Main function to handle command line arguments and execute the agent."""
    
    # Get review comments from the repository
    result = build_review_comment_dict(owner, repo)
    
    # Process each comment thread
    for key in result:
        comments_list = result[key]
        pr_id = comments_list[0]["pullRequestNumber"]
        line_number = comments_list[0]["line"]
        
        # Prepare the repository for analysis
        ensure_directory_exists()
        clear_directory(repo)
        clone_repo(owner, repo)
        result = checkout_pr(repo, pr_id)
        print(f"checkout_pr result: {result}")
        pr_diff = get_github_pr_diff(repo, pr_id)
        print(f"get_github_pr_diff result: {pr_diff}")
        # Create input message for the agent with all comments in the thread
        comments_text = ""
        for i, comment in enumerate(comments_list):
            comment_type = "Root Comment" if i == 0 else f"Reply #{i}"
            comments_text += f"""
{comment_type}:
- Author: {comment.get('author', 'Unknown')}
- Created: {comment.get('createdAt', 'Unknown')}
- Body: {comment.get('body', 'No comment body')}
- File: {comment.get('path', 'Unknown file')}
- Line: {comment.get('line', 'Unknown')}
"""
        
        user_message = f"""
Please analyze the GitHub repository {owner}/{repo} and the following review comment thread:

PR Number: {pr_id}
File: {comments_list[0].get('path', 'Unknown file')}
Line: {line_number}

PR Diff:
{pr_diff}

Comment Thread:
{comments_text}

Please analyze the entire comment thread along with the PR diff to understand the code changes and determine if this requires a reply, code changes, or no action. Consider the context of all comments in the thread and how they relate to the actual code changes.
"""
        
        # Invoke the LangGraph agent
        try:
            response = graph.invoke(
                {
                    "messages": [HumanMessage(content=user_message)],
                    "repo": repo
                },
                config={"configurable": {"user_name": "Developer"}}
            )
            
            print(f"Analysis for PR #{pr_id}:")
            print(f"Response: {response}")
            print("-" * 50)
            
        except Exception as e:
            print(f"Error processing PR #{pr_id}: {str(e)}")
            continue
        break # for now only process one comment thread

if __name__ == "__main__":
    parser = ArgumentParser(description="AI Agent for GitHub review comment addresal")
    
    # Add required arguments
    parser.add_argument(
        "--repo", 
        required=True, 
        help="Repository name (e.g., 'vscode')"
    )
    parser.add_argument(
        "--owner", 
        required=True, 
        help="Repository owner (username or organization, e.g., 'microsoft')"
    )
    
    # Parse arguments
    args = parser.parse_args()
    
    print(f"Repository: {args.owner}/{args.repo}")
    main(args.owner, args.repo)