from langchain_core.tools import tool
import os
import subprocess
import re


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
            
            if len(lines) == 0:
                return ""
            
            if end_line is None:
                end_line = len(lines) - 1
            else:
                end_line = min(end_line, len(lines) - 1)
            
            start_line = max(0, start_line)
            
            if start_line > end_line:
                return ""
            
            selected_lines = lines[start_line:end_line + 1]
            
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
    
    @tool
    def grep(search_pattern: str, file_pattern: str = "*", case_sensitive: bool = True, context_lines: int = 0) -> str:
        """
        Search for a pattern in files within the repository using grep.
        
        Args:
            search_pattern: The text pattern to search for (supports regex)
            file_pattern: File pattern to search in (e.g., "*.py", "*.js", "*.md")
            case_sensitive: Whether the search should be case sensitive (default: True)
            context_lines: Number of lines before and after each match to include (default: 0)
        
        Returns:
            String containing all matches with file names and line numbers
        """
        try:
            cmd = ["grep"]
            
            if context_lines > 0:
                cmd.extend(["-C", str(context_lines)])
            
            if not case_sensitive:
                cmd.append("-i")
            
            cmd.append("-n")
            cmd.append("-r")
            cmd.append(search_pattern)
            cmd.append(f"--include={file_pattern}")
            cmd.append(".")
            
            result = subprocess.run(
                cmd,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                if result.stdout.strip():
                    return f"Search results for '{search_pattern}':\n{result.stdout}"
                else:
                    return f"No matches found for pattern '{search_pattern}'"
            elif result.returncode == 1:
                return f"No matches found for pattern '{search_pattern}' in files matching '{file_pattern}'"
            else:
                return f"Error searching for pattern '{search_pattern}': {result.stderr}"
                
        except subprocess.TimeoutExpired:
            return f"Timeout while searching for pattern '{search_pattern}'"
        except Exception as e:
            return f"Error searching for pattern '{search_pattern}': {str(e)}"
    
    @tool
    def find_files(file_pattern: str, search_in_subdirs: bool = True) -> str:
        """
        Find files matching a pattern in the repository.
        
        Args:
            file_pattern: Pattern to match files (e.g., "*.py", "test_*.js", "*.md")
            search_in_subdirs: Whether to search in subdirectories (default: True)
        
        Returns:
            String containing list of matching files with their paths
        """
        try:
            if search_in_subdirs:
                cmd = ["find", ".", "-name", file_pattern, "-type", "f"]
            else:
                cmd = ["ls", "-1", file_pattern]
            
            result = subprocess.run(
                cmd,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                files = result.stdout.strip().split('\n')
                files = [f for f in files if f.strip()]
                
                if files:
                    file_list = "\n".join(files)
                    return f"Found {len(files)} file(s) matching '{file_pattern}':\n{file_list}"
                else:
                    return f"No files found matching pattern '{file_pattern}'"
            else:
                return f"Error finding files matching '{file_pattern}': {result.stderr}"
                
        except subprocess.TimeoutExpired:
            return f"Timeout while finding files matching '{file_pattern}'"
        except Exception as e:
            return f"Error finding files matching '{file_pattern}': {str(e)}"
    
    return [read_file, read_directory, grep, find_files]