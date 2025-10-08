#!/usr/bin/env python3
"""
Fetch review comment threads from all OPEN PRs in a GitHub repo using GraphQL.

Output structure:
{
  "<root_comment_id>": [
     {
       "id": "...child comment id...",
       "databaseId": 12345,
       "url": "https://github.com/...",
       "body": "comment text",
       "author": "login",
       "createdAt": "2025-01-01T12:34:56Z",
       "replyTo": "<parent_comment_id or None>",
       "path": "path/to/file.py",
       "pullRequestNumber": 42,
       "threadId": "<graphql thread id>"
     },
     ...
  ],
  ...
}

Root (top-level) comments appear only as keys, not inside any value list.
"""

import os
import subprocess
import sys
import time
import json
import argparse
from typing import Any, Dict, List, Optional
import requests
from dotenv import load_dotenv
load_dotenv()

GITHUB_API = "https://api.github.com/graphql"
GITHUB_TOKEN = os.getenv("GITHUB_API_KEY")

if not GITHUB_TOKEN:
    sys.stderr.write("Error: Please set GITHUB_TOKEN as an environment variable.\n")
    sys.exit(1)

HEADERS = {
    "Authorization": f"bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
}

# -------- GraphQL queries -------- #

Q_OPEN_PRS = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(
      states: OPEN
      first: 50
      after: $cursor
      orderBy: { field: UPDATED_AT, direction: DESC }
    ) {
      pageInfo { hasNextPage endCursor }
      nodes { number url }
    }
  }
}
"""

Q_PR_REVIEW_THREAD_IDS = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 50, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { id }
      }
    }
  }
}
"""

Q_THREAD_COMMENTS = """
query($id: ID!, $cursor: String) {
  node(id: $id) {
    ... on PullRequestReviewThread {
      id
      # Thread-level context (optional, handy to keep)
      path
      startLine
      line
      startDiffSide
      diffSide
      originalLine

      comments(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          databaseId
          url
          body
          createdAt
          path
          author { login }
          replyTo { id }

          # 💡 Line numbers on the file this comment applies to
          startLine
          line

          # 💡 Original line numbers at the time the comment was created
          originalStartLine
          originalLine

          # (Optional, deprecated diff positions—leave for debugging if you want)
          position
          originalPosition
        }
      }
    }
  }
}
"""

# GraphQL mutation to add a comment to a review thread (DEPRECATED - use M_ADD_REVIEW_THREAD_REPLY instead)
M_ADD_REVIEW_COMMENT = """
mutation($threadId: ID!, $body: String!) {
  addPullRequestReviewComment(input: {
    pullRequestReviewId: $threadId
    body: $body
  }) {
    comment {
      id
      databaseId
      url
      body
      createdAt
    }
  }
}
"""

# GraphQL mutation to reply to a review thread
M_ADD_REVIEW_THREAD_REPLY = """
mutation($input: AddPullRequestReviewThreadReplyInput!) {
  addPullRequestReviewThreadReply(input: $input) {
    comment {
      id
      url
      body
      createdAt
      author {
        login
      }
    }
  }
}
"""

# GraphQL query to get comment details and find its parent thread
Q_COMMENT_TO_PR = """
query($id: ID!) {
  node(id: $id) {
    __typename
    ... on PullRequestReviewComment {
      id
      pullRequest {
        number
        repository {
          name
          owner {
            login
          }
        }
      }
    }
  }
}
"""

# GraphQL query to get review threads for a PR
Q_REVIEW_THREADS = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 50, after: $after) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          id
          comments(first: 100) {
            nodes {
              id
            }
          }
        }
      }
    }
  }
}
"""

# -------- Helpers -------- #

def _post_graphql(query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    """POST a GraphQL query with basic error handling and light retry on flaky status codes."""
    for attempt in range(3):
        resp = requests.post(GITHUB_API, headers=HEADERS, json={"query": query, "variables": variables}, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            if "errors" in data:
                raise RuntimeError(f"GraphQL errors: {json.dumps(data['errors'], indent=2)}")
            return data["data"]
        # Retry on transient server errors
        if resp.status_code in (502, 503, 504) and attempt < 2:
            time.sleep(1.5 * (attempt + 1))
            continue
        raise RuntimeError(f"GraphQL HTTP {resp.status_code}: {resp.text}")
    raise RuntimeError("Unreachable")

def fetch_open_pr_numbers(owner: str, repo: str, pr_limit: Optional[int] = None) -> List[int]:
    cursor = None
    numbers: List[int] = []
    while True:
        data = _post_graphql(Q_OPEN_PRS, {"owner": owner, "name": repo, "cursor": cursor})
        repo_data = data.get("repository")
        if repo_data is None:
            raise RuntimeError(f"Repository not found: {owner}/{repo}")

        conn = repo_data["pullRequests"]
        for node in conn["nodes"]:
            numbers.append(node["number"])
            if pr_limit and len(numbers) >= pr_limit:
                return numbers

        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]

    return numbers

def fetch_review_thread_ids(owner: str, repo: str, pr_number: int) -> List[str]:
    cursor = None
    thread_ids: List[str] = []
    while True:
        data = _post_graphql(Q_PR_REVIEW_THREAD_IDS, {
            "owner": owner, "name": repo, "number": pr_number, "cursor": cursor
        })
        pr = data["repository"]["pullRequest"]
        if pr is None:
            # PR could have been closed/deleted between calls; skip gracefully
            return []

        conn = pr["reviewThreads"]
        thread_ids.extend([n["id"] for n in conn["nodes"]])

        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]
    return thread_ids

def fetch_thread_comments(thread_id: str) -> List[Dict[str, Any]]:
    cursor = None
    comments: List[Dict[str, Any]] = []
    while True:
        data = _post_graphql(Q_THREAD_COMMENTS, {"id": thread_id, "cursor": cursor})
        node = data.get("node")
        if node is None:
            break
        conn = node["comments"]
        comments.extend(conn["nodes"])
        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]
    return comments

def _shape_comment(c, pr_number, thread_id):
    return {
        "id": c["id"],
        "databaseId": c.get("databaseId"),
        "url": c.get("url"),
        "body": c.get("body", ""),
        "author": (c.get("author") or {}).get("login"),
        "createdAt": c.get("createdAt"),
        "replyTo": (c.get("replyTo") or {}).get("id"),
        "path": c.get("path"),
        "pullRequestNumber": pr_number,
        "threadId": thread_id,

        "startLine": c.get("startLine"),
        "line": c.get("line"),
        "originalStartLine": c.get("originalStartLine"),
        "originalLine": c.get("originalLine"),

        "position": c.get("position"),
        "originalPosition": c.get("originalPosition"),
    }

def build_review_comment_dict(owner: str, repo: str, pr_limit: Optional[int] = None) -> Dict[str, List[Dict[str, Any]]]:
    """
    Returns a dict mapping root review-comment IDs -> list of ALL comments in that thread,
    with the root comment included as the first element (followed by replies).
    """
    result: Dict[str, List[Dict[str, Any]]] = {}

    pr_numbers = fetch_open_pr_numbers(owner, repo, pr_limit=pr_limit)
    for pr_number in pr_numbers:
        thread_ids = fetch_review_thread_ids(owner, repo, pr_number)
        for t_id in thread_ids:
            comments = fetch_thread_comments(t_id)
            if not comments:
                continue

            # Sort for stable ordering
            comments_sorted = sorted(comments, key=lambda c: c.get("createdAt") or "")

            # Identify root (no replyTo). Fallback to earliest if none marked.
            roots = [c for c in comments_sorted if not c.get("replyTo")]
            root = roots[0] if roots else comments_sorted[0]
            root_id = root["id"]

            # Build full thread list: root first, then the rest
            thread_list = [_shape_comment(root, pr_number, t_id)] + [
                _shape_comment(c, pr_number, t_id)
                for c in comments_sorted
                if c["id"] != root_id
            ]

            result[root_id] = thread_list

    return result

def get_github_pr_diff(repo: str, pr_number: int) -> str:
    original_cwd = os.getcwd()
    os.chdir(f"/tmp/agent_repos/{repo}")
    result = subprocess.run(["gh", "pr", "diff", f"{pr_number}"], capture_output=True, text=True)
    os.chdir(original_cwd)
    return result.stdout

def clone_repo(owner, repo):
    original_cwd = os.getcwd()
    os.chdir(f"/tmp/agent_repos")
    result = subprocess.run(["gh", "repo", "clone", f"{owner}/{repo}"], capture_output=True, text=True)
    os.chdir(original_cwd)
    return result.stdout

def checkout_pr(repo: str, pr_number: int):
    original_cwd = os.getcwd()
    os.chdir(f"/tmp/agent_repos/{repo}")
    result = subprocess.run(["gh", "pr", "checkout", f"{pr_number}"], capture_output=True, text=True)
    os.chdir(original_cwd)
    return result.stdout

def create_new_branch(repo: str, pr_number: int, comment_node_id: str):
    original_cwd = os.getcwd()
    os.chdir(f"/tmp/agent_repos/{repo}")
    result = subprocess.run(["git", "checkout", "-b", f"pr-{pr_number}-response-{comment_node_id}"], capture_output=True, text=True)
    os.chdir(original_cwd)
    return result.stdout

def commit_changes(repo: str, commit_message: str = "update repo with changes from agent"):
    original_cwd = os.getcwd()
    os.chdir(f"/tmp/agent_repos/{repo}")
    result = subprocess.run(["git", "add", "."], capture_output=True, text=True)
    print(f"git add result: {result}")
    result = subprocess.run(["git", "commit", "-m", commit_message], capture_output=True, text=True)
    os.chdir(original_cwd)
    return result.stdout

def clear_directory(repo: str):
    original_cwd = os.getcwd()
    os.chdir(f"/tmp/agent_repos/")
    result = subprocess.run(["rm", "-rf", repo], capture_output=True, text=True)
    os.chdir(original_cwd)
    return result.stdout

def ensure_directory_exists():
    if not os.path.exists(f"/tmp/agent_repos/"):
        os.makedirs(f"/tmp/agent_repos/")

def add_comment_to_thread(thread_id: str = None, comment_id: str = None, comment_body: str = None) -> dict:
    """
    Reply to a PR review thread using the correct GraphQL mutation.
    
    Args:
        thread_id: The PullRequestReviewThread node ID to reply to (preferred)
        comment_id: A PullRequestReviewComment node ID (will find parent thread)
        comment_body: The text content of the comment to add
    
    Returns:
        Dictionary containing the result of the operation with comment details
    """
    try:
        if not comment_body:
            raise ValueError("comment_body is required")
        
        if not (thread_id or comment_id):
            raise ValueError("Provide either thread_id or comment_id")
        
        print(f"Adding comment to thread: {thread_id or 'from comment_id'}")
        print(f"Comment body: {comment_body}")
        
        # If we only have a comment_id, resolve its PR and then locate the thread containing that comment
        if comment_id and not thread_id:
            print(f"Looking up thread for comment_id: {comment_id}")
            
            # 1) Get the PR coordinates (owner/name/number) for this comment
            node_data = _post_graphql(Q_COMMENT_TO_PR, {"id": comment_id})
            node = node_data.get("node")
            
            if not node or node["__typename"] != "PullRequestReviewComment":
                raise ValueError("comment_id is not a PullRequestReviewComment node id")
            
            pr = node["pullRequest"]
            owner = pr["repository"]["owner"]["login"]
            name = pr["repository"]["name"]
            number = pr["number"]
            
            print(f"Found PR: {owner}/{name}#{number}")
            
            # 2) Walk the PR's reviewThreads to find which one contains this comment id
            after = None
            while True:
                threads_data = _post_graphql(Q_REVIEW_THREADS, {
                    "owner": owner,
                    "name": name,
                    "number": number,
                    "after": after
                })
                
                rt = threads_data["repository"]["pullRequest"]["reviewThreads"]
                for thread in rt["nodes"]:
                    if any(comment["id"] == comment_id for comment in thread["comments"]["nodes"]):
                        thread_id = thread["id"]
                        break
                
                if thread_id:
                    break
                    
                if rt["pageInfo"]["hasNextPage"]:
                    after = rt["pageInfo"]["endCursor"]
                else:
                    raise Exception("Could not locate the parent review thread for the given comment_id")
        
        # 3) Post the reply on the thread using the correct mutation
        payload = {
            "pullRequestReviewThreadId": thread_id,
            "body": comment_body
        }
        
        data = _post_graphql(M_ADD_REVIEW_THREAD_REPLY, {"input": payload})
        comment_data = data["addPullRequestReviewThreadReply"]["comment"]
        
        result = {
            "success": True,
            "comment_id": comment_data["id"],
            "comment_url": comment_data["url"],
            "comment_body": comment_data["body"],
            "created_at": comment_data["createdAt"],
            "author": comment_data["author"]["login"],
            "thread_id": thread_id
        }
        
        print(f"✅ Successfully added comment: {comment_data['url']}")
        return result
        
    except Exception as e:
        error_msg = f"Error adding comment to thread: {str(e)}"
        print(f"❌ {error_msg}")
        return {
            "success": False,
            "error": error_msg,
            "thread_id": thread_id,
            "comment_id": comment_id
        }

def reply_to_review(thread_id: str = None, comment_id: str = None, body: str = None) -> dict:
    """
    Convenience function that matches the GPT response interface.
    Reply to a PR review thread.
    
    Args:
        thread_id: The PullRequestReviewThread node ID to reply to (preferred)
        comment_id: A PullRequestReviewComment node ID (will find parent thread)
        body: The text content of the comment to add
    
    Returns:
        Dictionary containing the result of the operation with comment details
    """
    return add_comment_to_thread(thread_id=thread_id, comment_id=comment_id, comment_body=body)

def add_comment_to_comment_thread(comment_thread_data: dict, comment_body: str) -> dict:
    """
    Add a comment to a comment thread using the data structure from build_review_comment_dict.
    
    This is a convenience function that extracts the thread_id from the comment thread data
    and calls add_comment_to_thread.
    
    Args:
        comment_thread_data: A comment thread from build_review_comment_dict (the list of comments)
        comment_body: The text content of the comment to add
    
    Returns:
        Dictionary containing the result of the operation with comment details
    """
    try:
        # Get the thread_id from the first comment in the thread
        if not comment_thread_data or len(comment_thread_data) == 0:
            return {
                "success": False,
                "error": "Empty comment thread data provided"
            }
        
        first_comment = comment_thread_data[0]
        thread_id = first_comment.get("threadId")
        
        if not thread_id:
            return {
                "success": False,
                "error": "No threadId found in comment thread data"
            }
        
        print(f"Adding comment to thread for PR #{first_comment.get('pullRequestNumber', 'unknown')}")
        print(f"File: {first_comment.get('path', 'unknown')}")
        
        return add_comment_to_thread(thread_id=thread_id, comment_body=comment_body)
        
    except Exception as e:
        error_msg = f"Error processing comment thread data: {str(e)}"
        print(f"❌ {error_msg}")
        return {
            "success": False,
            "error": error_msg
        }

def push_changes_and_create_pr(repo: str, base_pr_number: int, new_branch_name: str = None, pr_title: str = None, pr_body: str = None) -> dict:
    """
    Push changes to a new branch and create a PR that targets the base PR's branch.
    
    This function assumes:
    1. The repository is already checked out to a new branch
    2. Changes have been made and committed to that branch
    3. The new branch needs to be pushed and a PR created targeting the base PR's branch
    
    Args:
        repo: Repository name (e.g., 'vscode')
        base_pr_number: The PR number that this new PR will target
        new_branch_name: Name for the new branch (optional, will use current branch if not provided)
        pr_title: Title for the new PR (optional, will be auto-generated if not provided)
        pr_body: Body/description for the new PR (optional, will be auto-generated if not provided)
    
    Returns:
        Dictionary containing the result of the operation with PR URL and details
    """
    original_cwd = os.getcwd()
    repo_path = f"/tmp/agent_repos/{repo}"
    
    try:
        # Change to the repository directory
        os.chdir(repo_path)
        
        # Get the base PR details to find the target branch
        print(f"Getting details for base PR #{base_pr_number}")
        pr_details_result = subprocess.run(
            ["gh", "pr", "view", str(base_pr_number), "--json", "headRefName,baseRefName,title"],
            capture_output=True, text=True, check=True
        )
        
        pr_details = json.loads(pr_details_result.stdout)
        base_branch = pr_details["headRefName"]  # The branch of the base PR
        base_pr_title = pr_details["title"]
        
        print(f"Base PR branch: {base_branch}")
        
        # Get current branch name if not provided
        if not new_branch_name:
            current_branch_result = subprocess.run(
                ["git", "branch", "--show-current"], 
                capture_output=True, text=True, check=True
            )
            new_branch_name = current_branch_result.stdout.strip()
        
        print(f"Using branch: {new_branch_name}")
        
        # Push the current branch to remote
        print(f"Pushing branch {new_branch_name} to remote")
        push_result = subprocess.run(
            ["git", "push", "-u", "origin", new_branch_name], 
            capture_output=True, text=True, check=True
        )
        
        # Generate PR title and body if not provided
        if not pr_title:
            pr_title = f"🤖 Agent fixes for PR #{base_pr_number}: {base_pr_title}"
        
        if not pr_body:
            pr_body = f"""## 🤖 Automated Code Review Response

This PR contains automated fixes and responses to comments in PR #{base_pr_number}.

### Changes Made
- Applied automated code fixes based on review comments
- Generated responses to review feedback

### Related PR
- Base PR: #{base_pr_number}
- Target Branch: `{base_branch}`

### Generated by
AI Agent for GitHub Code Review
"""
        
        # Create the PR
        print(f"Creating PR targeting branch: {base_branch}")
        pr_create_result = subprocess.run([
            "gh", "pr", "create",
            "--base", base_branch,
            "--head", new_branch_name,
            "--title", pr_title,
            "--body", pr_body
        ], capture_output=True, text=True, check=True)
        
        # Extract PR URL from the output
        pr_url = pr_create_result.stdout.strip()
        
        # Get the new PR number
        pr_number_result = subprocess.run([
            "gh", "pr", "view", pr_url, "--json", "number"
        ], capture_output=True, text=True, check=True)
        
        new_pr_number = json.loads(pr_number_result.stdout)["number"]
        
        result = {
            "success": True,
            "new_pr_number": new_pr_number,
            "new_pr_url": pr_url,
            "new_branch_name": new_branch_name,
            "base_pr_number": base_pr_number,
            "base_branch": base_branch,
            "pr_title": pr_title
        }
        
        print(f"✅ Successfully created PR #{new_pr_number}: {pr_url}")
        return result
        
    except subprocess.CalledProcessError as e:
        error_msg = f"Git/GitHub CLI error: {e.stderr}"
        print(f"❌ Error: {error_msg}")
        return {
            "success": False,
            "error": error_msg,
            "command": e.cmd,
            "return_code": e.returncode
        }
    except json.JSONDecodeError as e:
        error_msg = f"JSON parsing error: {str(e)}"
        print(f"❌ Error: {error_msg}")
        return {
            "success": False,
            "error": error_msg
        }
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        print(f"❌ Error: {error_msg}")
        return {
            "success": False,
            "error": error_msg
        }
    finally:
        # Always return to original directory
        os.chdir(original_cwd)

def main():
    ap = argparse.ArgumentParser(description="Extract review comment threads from open PRs in a repo.")
    ap.add_argument("owner", help="Repository owner/org (e.g., 'facebook')")
    ap.add_argument("repo", help="Repository name (e.g., 'react')")
    ap.add_argument("--pr-limit", type=int, default=None, help="Only scan the first N open PRs (optional)")
    ap.add_argument("--out", default=None, help="Write JSON output to a file (optional)")
    args = ap.parse_args()

    data = build_review_comment_dict(args.owner, args.repo, pr_limit=args.pr_limit)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Wrote {len(data)} root threads to {args.out}")
    else:
        print(json.dumps(data, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()