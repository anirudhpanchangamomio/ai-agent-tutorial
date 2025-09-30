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

def create_new_branch(repo: str, pr_number: int):
    original_cwd = os.getcwd()
    os.chdir(f"/tmp/agent_repos/{repo}")
    result = subprocess.run(f"git checkout -b pr-{pr_number}", capture_output=True, text=True)
    os.chdir(original_cwd)
    return result.stdout

def commit_changes(repo: str):
    original_cwd = os.getcwd()
    os.chdir(f"/tmp/agent_repos/{repo}")
    result = subprocess.run(["git", "commit", "-m", "update repo with changes from agent"], capture_output=True, text=True)
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
