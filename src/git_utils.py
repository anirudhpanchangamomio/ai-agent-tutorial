from dotenv import load_dotenv
load_dotenv()
from github import Github, Auth
import os


def get_github_client():
    """Initialize GitHub client"""
    api_key = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_API_KEY")
    if not api_key:
        raise Exception("GitHub token not found. Please set the GITHUB_TOKEN or GITHUB_API_KEY environment variable. "
                        "You can get a token at https://github.com/settings/tokens")
    return Github(auth=Auth.Token(api_key))


def fetch_all_pr_comments(owner, repo_name):
    """
    Fetch all comments from all open Pull Requests in a GitHub repository.
    
    Args:
        owner (str): The owner of the repository (username or organization)
        repo_name (str): The name of the repository
        
    Returns:
        dict: A dictionary containing PR information and their comments
        Format: {
            'pr_number': {
                'pr_info': {...},  # PR details
                'comments': [...]  # List of comments
            }
        }
    """
    try:
        # Initialize GitHub client
        g = get_github_client()
        
        # Get the repository
        repo = g.get_repo(f"{owner}/{repo_name}")
        
        # Get all open pull requests
        open_prs = repo.get_pulls(state='open')
        
        all_comments = {}
        
        print(f"Found {open_prs.totalCount} open pull requests in {owner}/{repo_name}")
        
        for pr in open_prs:
            pr_number = pr.number
            print(f"Processing PR #{pr_number}: {pr.title}")
            
            # Get PR information
            pr_info = {
                'number': pr.number,
                'title': pr.title,
                'body': pr.body,
                'state': pr.state,
                'created_at': pr.created_at,
                'updated_at': pr.updated_at,
                'user': pr.user.login if pr.user else None,
                'head_branch': pr.head.ref,
                'base_branch': pr.base.ref,
                'url': pr.html_url
            }
            
            # Get all comments for this PR
            comments = []
            
            # Get review comments (comments on code)
            review_comments = pr.get_review_comments()
            for comment in review_comments:
                comments.append({
                    'type': 'review_comment',
                    'id': comment.id,
                    'body': comment.body,
                    'user': comment.user.login if comment.user else None,
                    'created_at': comment.created_at,
                    'updated_at': comment.updated_at,
                    'path': comment.path,
                    'position': comment.position,
                    'line': comment.line,
                    'original_position': comment.original_position,
                    'original_line': comment.original_line,
                    'url': comment.html_url
                })
            
            # Get issue comments (general comments on the PR)
            issue_comments = pr.get_issue_comments()
            for comment in issue_comments:
                comments.append({
                    'type': 'issue_comment',
                    'id': comment.id,
                    'body': comment.body,
                    'user': comment.user.login if comment.user else None,
                    'created_at': comment.created_at,
                    'updated_at': comment.updated_at,
                    'url': comment.html_url
                })
            
            # Get review comments (from reviews)
            reviews = pr.get_reviews()
            for review in reviews:
                if review.body:  # Only include reviews with comments
                    comments.append({
                        'type': 'review',
                        'id': review.id,
                        'body': review.body,
                        'user': review.user.login if review.user else None,
                        'created_at': review.submitted_at,
                        'state': review.state,
                        'url': review.html_url
                    })
            
            # Sort comments by creation date
            comments.sort(key=lambda x: x['created_at'])
            
            all_comments[pr_number] = {
                'pr_info': pr_info,
                'comments': comments,
                'total_comments': len(comments)
            }
            
            print(f"  - Found {len(comments)} comments")
        
        print(f"Completed processing all open PRs. Total PRs: {len(all_comments)}")
        return all_comments
        
    except Exception as e:
        print(f"Error fetching PR comments: {str(e)}")
        raise


def fetch_pr_comments_summary(owner, repo_name):
    """
    Fetch a summary of comments from all open Pull Requests.
    
    Args:
        owner (str): The owner of the repository
        repo_name (str): The name of the repository
        
    Returns:
        dict: Summary information about PRs and their comments
    """
    all_comments = fetch_all_pr_comments(owner, repo_name)
    
    summary = {
        'total_open_prs': len(all_comments),
        'total_comments': sum(pr_data['total_comments'] for pr_data in all_comments.values()),
        'prs_with_comments': len([pr for pr in all_comments.values() if pr['total_comments'] > 0]),
        'prs_without_comments': len([pr for pr in all_comments.values() if pr['total_comments'] == 0]),
        'pr_details': []
    }
    
    for pr_number, pr_data in all_comments.items():
        pr_info = pr_data['pr_info']
        summary['pr_details'].append({
            'number': pr_number,
            'title': pr_info['title'],
            'author': pr_info['user'],
            'created_at': pr_info['created_at'],
            'comment_count': pr_data['total_comments'],
            'url': pr_info['url']
        })
    
    # Sort by comment count (descending)
    summary['pr_details'].sort(key=lambda x: x['comment_count'], reverse=True)
    
    return summary


