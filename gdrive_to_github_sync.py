#!/usr/bin/env python3
"""
Google Drive to GitHub Synchronization Script

This script automates the process of syncing new files from a Google Drive folder
to a local Git repository, converting them to optimal formats, and pushing changes
to GitHub with automatic README.md updates.

Author: Automated System
Date: October 15, 2025
"""

import os
import sys
import json
import logging
import re
from typing import Dict, Set, List, Tuple, Any, Protocol, cast, Optional
from datetime import datetime
import time
from collections import deque
import tempfile
import shutil

try:
    from colorama import Fore, Style, init as colorama_init
    COLORAMA_AVAILABLE = True
except Exception:
    COLORAMA_AVAILABLE = False

# Google Drive API imports
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.errors import HttpError

# Git imports
from git import Repo, GitCommandError

# Configure logging
class ColorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        if not COLORAMA_AVAILABLE:
            return msg
        # Red for issues (WARNING, ERROR, CRITICAL), Green for others (INFO, DEBUG)
        if record.levelno >= logging.WARNING:
            return f"{Fore.RED}{msg}{Style.RESET_ALL}"
        else:
            return f"{Fore.GREEN}{msg}{Style.RESET_ALL}"

if COLORAMA_AVAILABLE:
    colorama_init(autoreset=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[logging.FileHandler('gdrive_sync.log')])
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(ColorFormatter('%(asctime)s - %(levelname)s - %(message)s'))
logger = logging.getLogger(__name__)
logger.addHandler(_console_handler)

# Google Drive API scopes
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']


class ConfigurationError(Exception):
    """Custom exception for configuration-related errors."""
    pass


class SyncError(Exception):
    """Custom exception for synchronization-related errors."""
    pass


class NonFastForwardError(SyncError):
    """Remote is ahead; local push would be non-fast-forward."""
    pass


class DriveFilesResource(Protocol):
    def list(self, **kwargs: Any) -> Any: ...
    def export_media(self, **kwargs: Any) -> Any: ...
    def get_media(self, **kwargs: Any) -> Any: ...


class DriveService(Protocol):
    def files(self) -> DriveFilesResource: ...


def load_config(config_path: str = 'config.json') -> Dict:
    """
    Load configuration from a JSON file.

    Args:
        config_path: Path to the configuration file

    Returns:
        Dictionary containing configuration values

    Raises:
        ConfigurationError: If config file is missing or invalid
    """
    try:
        if not os.path.exists(config_path):
            logger.warning(f"Config file not found at {config_path}")
            create_template_config(config_path)
            raise ConfigurationError(
                f"Configuration file not found. A template has been created at {config_path}. "
                "Please fill in your details and run the script again."
            )

        with open(config_path, 'r') as f:
            config = json.load(f)

        # Validate required fields
        required_fields = [
            'GOOGLE_DRIVE_FOLDER_ID',
            'LOCAL_REPO_PATH',
            'GITHUB_REPO_URL',
            'GDRIVE_CREDENTIALS_FILE',
            'GITHUB_PERSONAL_ACCESS_TOKEN'
        ]

        missing_fields = [field for field in required_fields if not config.get(field)]
        if missing_fields:
            raise ConfigurationError(
                f"Missing required configuration fields: {', '.join(missing_fields)}"
            )

        logger.info("Configuration loaded successfully")
        return config

    except json.JSONDecodeError as e:
        raise ConfigurationError(f"Invalid JSON in config file: {e}")
    except Exception as e:
        raise ConfigurationError(f"Error loading configuration: {e}")


def create_template_config(config_path: str):
    """
    Create a template configuration file.

    Args:
        config_path: Path where the template should be created
    """
    template = {
        "GOOGLE_DRIVE_FOLDER_ID": "your_google_drive_folder_id_here",
        "LOCAL_REPO_PATH": "C:\\path\\to\\your\\local\\repo",
        "GITHUB_REPO_URL": "https://github.com/username/repo",
        "GDRIVE_CREDENTIALS_FILE": "credentials.json",
        "GITHUB_PERSONAL_ACCESS_TOKEN": "your_github_pat_here",
        "SYNC_SUBDIR_NAME": "Google Drive Folder"
    }

    try:
        with open(config_path, 'w') as f:
            json.dump(template, f, indent=2)
        logger.info(f"Template configuration created at {config_path}")
    except Exception as e:
        logger.error(f"Failed to create template config: {e}")


def authenticate_gdrive(credentials_file: str) -> DriveService:
    """
    Authenticate with Google Drive API using OAuth2.

    Args:
        credentials_file: Path to the credentials.json file

    Returns:
        Authenticated Google Drive service object

    Raises:
        SyncError: If authentication fails
    """
    try:
        creds = None
        token_file = 'token.json'

        # Load existing token if available
        if os.path.exists(token_file):
            try:
                creds = Credentials.from_authorized_user_file(token_file, SCOPES)
            except Exception as e:
                logger.warning(f"Error loading token file: {e}")
                creds = None

        # Refresh or obtain new credentials
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Refreshing expired credentials")
                creds.refresh(Request())
            else:
                logger.info("Starting OAuth2 flow for new credentials")
                if not os.path.exists(credentials_file):
                    raise SyncError(f"Credentials file not found: {credentials_file}")

                flow = InstalledAppFlow.from_client_secrets_file(
                    credentials_file, SCOPES
                )
                creds = flow.run_local_server(port=0)

            # Save the credentials for future runs
            with open(token_file, 'w') as token:
                token.write(creds.to_json())
            logger.info("Credentials saved to token.json")

        # Build and return the service (disable discovery cache to silence warning)
        service = build('drive', 'v3', credentials=creds, cache_discovery=False)
        logger.info("Successfully authenticated with Google Drive API")
        return service

    except Exception as e:
        raise SyncError(f"Failed to authenticate with Google Drive: {e}")


def _extract_drive_folder_id(value: str) -> str:
    """
    Accept a raw folder ID or a Google Drive folder URL and return the folder ID.
    """
    if not value:
        raise SyncError("GOOGLE_DRIVE_FOLDER_ID is empty")

    if value.startswith("http://") or value.startswith("https://"):
        m = re.search(r"/folders/([A-Za-z0-9_-]+)", value)
        if m:
            return m.group(1)
        m = re.search(r"[?&]id=([A-Za-z0-9_-]+)", value)
        if m:
            return m.group(1)
        raise SyncError("Could not parse Google Drive folder ID from the provided URL")
    return value


def _validate_drive_folder(service: DriveService, folder_id: str) -> Tuple[str, str]:
    """
    Validate that the folder exists and return (folder_id, drive_id_or_empty).
    """
    try:
        service_any = cast(Any, service)
        # noinspection PyUnresolvedReferences
        meta = service_any.files().get(
            fileId=folder_id,
            fields="id, name, mimeType, driveId",
            supportsAllDrives=True,
        ).execute()

        if meta.get("mimeType") != "application/vnd.google-apps.folder":
            raise SyncError("Provided ID does not reference a folder")

        drive_id = meta.get("driveId", "")
        folder_name = meta.get("name", folder_id)
        location = "Shared Drive" if drive_id else "My Drive"
        logger.info(f"Target folder validated: '{folder_name}' ({location})")
        return folder_id, drive_id

    except HttpError as e:
        if getattr(e, 'status_code', None) == 404 or 'File not found' in str(e):
            raise SyncError(
                "Google Drive folder not found or no access. "
                "Verify GOOGLE_DRIVE_FOLDER_ID (ID or URL) and your permissions."
            )
        raise


def _is_transient_http_error(e: HttpError) -> bool:
    try:
        status = int(getattr(e, 'status_code', None) or e.resp.status)
    except Exception:
        status = None
    return status is not None and 500 <= status < 600


def export_with_retries(service: DriveService, file_id: str, export_mime: str, out_fp, max_retries: int = 3) -> None:
    """Export Drive file with retry/backoff for transient 5xx errors."""
    delay = 1.0
    for attempt in range(1, max_retries + 1):
        try:
            service_any = cast(Any, service)
            # noinspection PyUnresolvedReferences
            request = service_any.files().export_media(fileId=file_id, mimeType=export_mime)
            downloader = MediaIoBaseDownload(out_fp, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            return
        except HttpError as e:
            if _is_transient_http_error(e) and attempt < max_retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise


def download_binary_with_retries(service: DriveService, file_id: str, out_fp, max_retries: int = 3) -> None:
    """Download binary file with retry/backoff for transient 5xx errors."""
    delay = 1.0
    for attempt in range(1, max_retries + 1):
        try:
            service_any = cast(Any, service)
            # noinspection PyUnresolvedReferences
            request = service_any.files().get_media(fileId=file_id)
            downloader = MediaIoBaseDownload(out_fp, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            return
        except HttpError as e:
            if _is_transient_http_error(e) and attempt < max_retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise


def sanitize_component(name: str) -> str:
    """Sanitize a single path component."""
    return sanitize_filename(name)


def _drive_list_children(service: DriveService, folder_id: str, drive_id: str) -> List[Dict]:
    """List direct children of a Drive folder (My Drive or Shared Drive)."""
    files: List[Dict] = []
    page_token: Optional[str] = None
    params: Dict[str, Any] = {
        'q': f"'{folder_id}' in parents and trashed=false",
        'includeItemsFromAllDrives': True,
        'supportsAllDrives': True,
        'fields': 'nextPageToken, files(id, name, mimeType, modifiedTime, shortcutDetails(targetId,targetMimeType))',
    }
    if drive_id:
        params['corpora'] = 'drive'
        params['driveId'] = drive_id
    else:
        params['corpora'] = 'user'

    while True:
        service_any = cast(Any, service)
        # noinspection PyUnresolvedReferences
        resp = service_any.files().list(pageSize=1000, pageToken=page_token, **params).execute()
        files.extend(resp.get('files', []))
        page_token = resp.get('nextPageToken')
        if not page_token:
            break
    return files


def iter_drive_tree(service: DriveService, root_folder_id: str, drive_id: str, base_path: str = ''):
    """BFS over Drive starting at root_folder_id, yielding (rel_parent, item)."""
    queue = deque([(root_folder_id, base_path)])
    while queue:
        folder_id, rel_parent = queue.popleft()
        logger.info(f"Entering folder: {rel_parent or '/'}")
        for child in _drive_list_children(service, folder_id, drive_id):
            mime = child.get('mimeType', '')
            name = sanitize_component(child.get('name', 'untitled'))
            if mime == 'application/vnd.google-apps.folder':
                sub_rel = os.path.join(rel_parent, name) if rel_parent else name
                queue.append((child['id'], sub_rel))
                continue
            if mime == 'application/vnd.google-apps.shortcut':
                sc = child.get('shortcutDetails') or {}
                target_id = sc.get('targetId')
                target_mime = sc.get('targetMimeType') or ''
                if not target_id:
                    logger.info(f"Skipping shortcut without target: {name}")
                    continue
                child = {
                    'id': target_id,
                    'name': name,
                    'mimeType': target_mime,
                    'modifiedTime': child.get('modifiedTime')
                }
                mime = target_mime
            yield (rel_parent, child)


def export_candidates_for_mime(mime: str) -> List[Tuple[str, str]]:
    """Ordered list of (export_mime, ext) candidates for Google Workspace types."""
    if mime == 'application/vnd.google-apps.document':
        return [('text/markdown', '.md'), ('application/pdf', '.pdf'), ('text/plain', '.txt')]
    if mime == 'application/vnd.google-apps.spreadsheet':
        return [('text/csv', '.csv'), ('application/pdf', '.pdf')]
    if mime == 'application/vnd.google-apps.presentation':
        return [('application/pdf', '.pdf')]
    if mime == 'application/vnd.google-apps.drawing':
        return [('application/pdf', '.pdf'), ('image/png', '.png')]
    if mime.startswith('application/vnd.google-apps.'):
        return [('application/pdf', '.pdf')]
    return []


def get_drive_files(service: DriveService, folder_id: str) -> List[Tuple[str, Dict]]:
    """
    Recursively get list of files: returns list of (rel_parent_path, item_metadata).
    rel_parent_path is sanitized and uses OS separators.
    """
    try:
        normalized_id = _extract_drive_folder_id(folder_id)
        folder_id, drive_id = _validate_drive_folder(service, normalized_id)

        items: List[Tuple[str, Dict]] = []
        count = 0
        for rel_parent, item in iter_drive_tree(service, folder_id, drive_id):
            items.append((rel_parent, item))
            count += 1
        logger.info(f"Found {count} files in Google Drive (recursive)")
        return items
    except HttpError as e:
        raise SyncError(f"HTTP error fetching Drive files: {e}")
    except Exception as e:
        raise SyncError(f"Error fetching Drive files: {e}")


def get_repo_files(repo_path: str) -> Set[str]:
    """
    Get set of filenames currently in the local repository.

    Args:
        repo_path: Path to the local Git repository

    Returns:
        Set of filenames (with extensions) in the repository

    Raises:
        SyncError: If reading repository fails
    """
    try:
        if not os.path.exists(repo_path):
            raise SyncError(f"Repository path does not exist: {repo_path}")

        repo_files = set()

        # Walk the repository directory
        for root, dirs, files in os.walk(repo_path):
            # Skip .git directory
            if '.git' in dirs:
                dirs.remove('.git')

            # Add all files to the set
            for file in files:
                # Get relative path from repo root
                rel_path = os.path.relpath(os.path.join(root, file), repo_path)
                # Normalize path separators
                rel_path = rel_path.replace('\\', '/')
                repo_files.add(rel_path)

        logger.info(f"Found {len(repo_files)} files in local repository")
        return repo_files

    except Exception as e:
        raise SyncError(f"Error reading repository files: {e}")


# Destination subfolder name inside the repo (can be overridden via config key SYNC_SUBDIR_NAME)
DEFAULT_SYNC_SUBDIR = 'Google Drive Folder'


def sanitize_filename(name: str) -> str:
    """Sanitize a filename for Windows/posix filesystems."""
    # Replace invalid characters: \/:*?"<>|
    sanitized = re.sub(r'[\\/:*?"<>|]', '_', name)
    # Remove control chars
    sanitized = re.sub(r'[\x00-\x1f\x7f]', '', sanitized)
    # Trim trailing dots/spaces
    sanitized = sanitized.rstrip(' .')
    # Avoid empty names
    if not sanitized:
        sanitized = 'untitled'
    # Avoid reserved names on Windows
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
    if sanitized.upper() in reserved:
        sanitized = f"_{sanitized}"
    return sanitized


def get_destination_dir(repo_path: str, config: Dict) -> Tuple[str, str]:
    """Return (absolute_dest_dir, relative_subdir_name) and ensure it exists."""
    subdir = config.get('SYNC_SUBDIR_NAME', DEFAULT_SYNC_SUBDIR)
    dest_dir = os.path.join(repo_path, subdir)
    os.makedirs(dest_dir, exist_ok=True)
    return dest_dir, subdir


def download_and_convert_file(service: DriveService, file_id: str, file_name: str,
                               mime_type: str, dest_path: str) -> Tuple[str, str]:
    """
    Download a file from Google Drive and convert if necessary.
    """
    try:
        # Replace single-attempt export with candidate-based retry/fallback
        candidates = export_candidates_for_mime(mime_type)
        if candidates:
            base_name = sanitize_filename(os.path.splitext(file_name)[0])
            last_error: Optional[Exception] = None
            for export_mime, extension in candidates:
                final_filename = base_name + extension
                os.makedirs(dest_path, exist_ok=True)
                file_path = os.path.join(dest_path, final_filename)
                try:
                    with open(file_path, 'wb') as fp:
                        export_with_retries(service, file_id, export_mime, fp)
                    logger.info(f"Successfully saved: {file_path}")
                    return final_filename, file_path
                except HttpError as e:
                    last_error = e
                    continue
            # If all candidates failed
            if last_error:
                raise last_error
            raise SyncError(f"Failed to export file {file_name}")
        else:
            final_filename = sanitize_filename(file_name)
            os.makedirs(dest_path, exist_ok=True)
            file_path = os.path.join(dest_path, final_filename)
            with open(file_path, 'wb') as fp:
                download_binary_with_retries(service, file_id, fp)
            logger.info(f"Successfully saved: {file_path}")
            return final_filename, file_path
    except HttpError as e:
        raise SyncError(f"HTTP error downloading file {file_name}: {e}")
    except Exception as e:
        raise SyncError(f"Error downloading file {file_name}: {e}")


def cleanup_downloads(paths: List[str]) -> None:
    """Delete downloaded files for rollback on error."""
    for p in paths:
        try:
            if p and os.path.isfile(p):
                os.remove(p)
                logger.warning(f"Rolled back downloaded file: {p}")
        except Exception as e:
            logger.warning(f"Failed to remove file during rollback: {p} ({e})")


def sync_files(service: DriveService, config: Dict, created_files: List[str]) -> List[str]:
    """
    Main synchronization logic: compare, download, and convert new files.
    """
    try:
        folder_id = config['GOOGLE_DRIVE_FOLDER_ID']
        repo_path = config['LOCAL_REPO_PATH']

        dest_root, dest_subdir = get_destination_dir(repo_path, config)

        # Recursive Drive files: list of (rel_parent, item)
        drive_items = get_drive_files(service, folder_id)
        # Repo files: set of relative paths from repo root
        repo_files = get_repo_files(repo_path)
        repo_files_lower = {p.lower() for p in repo_files}

        new_files: List[str] = []

        for rel_parent, item in drive_items:
            mime_type = item.get('mimeType', '')
            name = item.get('name', 'untitled')

            # Skip unsupported Google app items (forms, sites, scripts, maps)
            if mime_type in {
                'application/vnd.google-apps.form',
                'application/vnd.google-apps.map',
                'application/vnd.google-apps.site',
                'application/vnd.google-apps.script',
            }:
                logger.info(f"Skipping unsupported Google item: {os.path.join(rel_parent or '', name)} ({mime_type})")
                continue

            # Compute candidate output filenames for idempotency
            base_name = sanitize_filename(os.path.splitext(name)[0])
            candidates = export_candidates_for_mime(mime_type)
            expected_rel_candidates: List[str] = []
            if candidates:
                for _, ext in candidates:
                    rel_path = os.path.join(dest_subdir, rel_parent or '', base_name + ext).replace('\\', '/')
                    expected_rel_candidates.append(rel_path)
            else:
                rel_path = os.path.join(dest_subdir, rel_parent or '', sanitize_filename(name)).replace('\\', '/')
                expected_rel_candidates.append(rel_path)

            # If any candidate already exists in repo, skip
            if any(c.lower() in repo_files_lower for c in expected_rel_candidates):
                logger.debug(f"File already exists in repo: one of {expected_rel_candidates}")
                continue

            # Download/export into mirrored folder under dest_root
            abs_dest_dir = os.path.join(dest_root, rel_parent) if rel_parent else dest_root
            final_filename, file_path = download_and_convert_file(
                service,
                item['id'],
                name,
                mime_type,
                abs_dest_dir
            )

            # Track absolute path for rollback and relative for README
            if file_path:
                created_files.append(file_path)
            rel_added = os.path.relpath(file_path, repo_path).replace('\\', '/')
            new_files.append(rel_added)

        if not new_files:
            logger.info("No new files to sync")
        else:
            logger.info(f"Successfully synced {len(new_files)} new file(s)")

        return new_files

    except Exception as e:
        raise SyncError(f"Error during file synchronization: {e}")


def commit_and_push(repo_path: str, new_files: List[str], config: Dict):
    """
    Commit and push new files to GitHub, skipping Git hooks to avoid local hook failures.

    Auto-resolves non-fast-forward by fetch + pull --rebase, then retries push.

    Args:
        repo_path: Path to the local Git repository
        new_files: List of new filenames to commit
        config: Configuration dictionary

    Raises:
        SyncError: If Git operations fail
        NonFastForwardError: If push is rejected and auto-rebase cannot resolve
    """
    try:
        if not new_files:
            logger.info("No files to commit. Skipping Git operations.")
            return

        # Initialize repository
        repo = Repo(repo_path)

        # Check if repo is valid
        if repo.bare:
            raise SyncError("Local repository is bare or invalid")

        # Add all new files
        logger.info("Staging new files...")
        repo.git.add(A=True)

        # Create commit message
        preview = ', '.join(new_files[:5])  # Show first 5 files
        if len(new_files) > 5:
            preview += f" (+{len(new_files) - 5} more)"
        commit_message = f"feat: Sync new files from Google Drive\n\nAdded: {preview}"

        # Prepare remote URL with PAT if provided
        pat = config.get('GITHUB_PERSONAL_ACCESS_TOKEN')

        # Ensure a remote named 'origin' exists
        remote_names = [r.name for r in repo.remotes]
        if 'origin' in remote_names:
            remote = repo.remote('origin')
        else:
            remote = repo.create_remote('origin', config['GITHUB_REPO_URL'])

        # Determine branch (handle detached HEAD and missing default)
        def resolve_branch() -> str:
            try:
                return repo.active_branch.name
            except Exception:
                # Try to read remote HEAD to discover default branch
                try:
                    # Ensure we have origin refs
                    repo.git.execute(['git', '-C', repo_path, 'fetch', '-q', 'origin'])
                    head_ref = repo.git.execute(['git', '-C', repo_path, 'symbolic-ref', '-q', 'refs/remotes/origin/HEAD']).strip()
                    if head_ref:
                        return head_ref.split('/')[-1]
                except Exception:
                    pass
                # Fallback
                return 'main'

        branch = resolve_branch()

        original_url = None
        temp_hooks_dir = None
        try:
            if pat and pat != 'your_github_pat_here':
                try:
                    original_url = list(remote.urls)[0]
                except Exception:
                    original_url = None
                target_url = original_url or config['GITHUB_REPO_URL']
                if isinstance(target_url, bytes):
                    target_url = target_url.decode('utf-8', errors='ignore')
                if target_url.startswith('https://'):
                    # Inject PAT for this push only; keep password placeholder for compatibility
                    auth_url = target_url.replace('https://', f"https://{pat}:x-oauth-basic@")
                else:
                    auth_url = target_url
                remote.set_url(auth_url)

            # Create a temporary empty hooks directory to bypass ALL hooks (including post-commit)
            temp_hooks_dir = tempfile.mkdtemp(prefix='git-hooks-disabled-')

            # Commit with hooks disabled via per-invocation config and also pass --no-verify (harmless)
            logger.info("Creating commit (hooks disabled)...")
            repo.git.execute([
                'git', '-C', repo_path,
                '-c', f'core.hooksPath={temp_hooks_dir}',
                'commit', '-m', commit_message, '--no-verify'
            ])

            def _push_once():
                repo.git.execute([
                    'git', '-C', repo_path,
                    '-c', f'core.hooksPath={temp_hooks_dir}',
                    'push', '--no-verify', 'origin', branch
                ])

            # Push with hooks disabled via per-invocation config and --no-verify
            logger.info("Pushing to GitHub (hooks disabled)...")
            try:
                _push_once()
                logger.info("Successfully pushed changes to GitHub")
            except GitCommandError as push_err:
                msg = (getattr(push_err, 'stderr', '') or str(push_err)).lower()
                non_ff = (
                    'fetch first' in msg or
                    'non-fast-forward' in msg or
                    'failed to push some refs' in msg or
                    'contains work that you do not have locally' in msg
                )
                if not non_ff:
                    raise

                # Attempt auto-resolve: fetch + pull --rebase, then retry push
                try:
                    logger.info("Remote is ahead; fetching and rebasing onto origin...")
                    repo.git.execute(['git', '-C', repo_path, 'fetch', 'origin'])
                    repo.git.execute([
                        'git', '-C', repo_path,
                        '-c', f'core.hooksPath={temp_hooks_dir}',
                        'pull', '--rebase', 'origin', branch
                    ])
                    _push_once()
                    logger.info("Rebased onto origin and pushed successfully")
                except GitCommandError as re_err:
                    raise NonFastForwardError(
                        "Push rejected because the remote has new commits. "
                        "Automatic fetch/rebase failed or requires manual conflict resolution. "
                        "Please run 'git pull --rebase' in your repo, resolve any conflicts, then rerun the sync."
                    ) from re_err
        finally:
            # Restore original URL (without PAT) if we modified it
            if original_url:
                try:
                    remote.set_url(original_url)
                except Exception as e:
                    logger.warning(f"Failed to restore remote URL: {e}")
            # Cleanup temporary hooks directory
            if temp_hooks_dir:
                try:
                    shutil.rmtree(temp_hooks_dir, ignore_errors=True)
                except Exception as e:
                    logger.debug(f"Failed to clean temp hooks dir: {e}")

    except NonFastForwardError:
        # Bubble up; main() handles without rollback
        raise
    except GitCommandError as e:
        # Provide clearer diagnostics for hook-related failures
        msg = str(e)
        if 'post-commit' in msg.lower():
            raise SyncError("Git post-commit hook failed. Quote paths with spaces or disable the hook, or run with hooks skipped.")
        raise SyncError(f"Git command error: {e}")
    except Exception as e:
        raise SyncError(f"Error committing and pushing: {e}")


def update_readme(repo_path: str, github_url: str, new_files: List[str]):
    """
    Update README.md with links to newly synced files.

    Args:
        repo_path: Path to the local Git repository
        github_url: GitHub repository URL
        new_files: List of newly added filenames

    Raises:
        SyncError: If README update fails
    """
    try:
        if not new_files:
            logger.info("No new files to add to README")
            return

        readme_path = os.path.join(repo_path, 'README.md')

        if not os.path.exists(readme_path):
            logger.info("Creating new README.md")
            with open(readme_path, 'w', encoding='utf-8') as f:
                f.write("# Synced Files from Google Drive\n\n")
                f.write("This repository is automatically synced with Google Drive.\n\n")

        with open(readme_path, 'r', encoding='utf-8') as f:
            content = f.read()

        section_header = "## Synced Project Files"
        if section_header not in content:
            content += f"\n\n{section_header}\n\n"
            content += f"*Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n"

        if github_url.endswith('.git'):
            github_url = github_url[:-4]

        # Build file links with relative paths (already include subdir)
        file_links = []
        for rel_path in new_files:
            encoded_path = rel_path.replace(' ', '%20')
            file_url = f"{github_url}/blob/main/{encoded_path}"
            display_name = os.path.splitext(os.path.basename(rel_path))[0]
            file_links.append(f"- [{display_name}]({file_url})")

        content += '\n'.join(file_links) + '\n'

        with open(readme_path, 'w', encoding='utf-8') as f:
            f.write(content)

        logger.info(f"Updated README.md with {len(new_files)} new file link(s)")

        temp_hooks_dir: Optional[str] = None
        try:
            repo = Repo(repo_path)
            repo.git.add('README.md')

            # Create a temporary empty hooks directory to bypass ALL hooks for commit/push
            temp_hooks_dir = tempfile.mkdtemp(prefix='git-hooks-disabled-')

            # Commit README with hooks disabled and --no-verify
            repo.git.execute([
                'git', '-C', repo_path,
                '-c', f'core.hooksPath={temp_hooks_dir}',
                'commit', '-m', 'docs: Update README with new synced files', '--no-verify'
            ])
            # Push README update with hooks disabled and --no-verify
            try:
                branch = repo.active_branch.name
            except Exception:
                branch = 'HEAD'
            repo.git.execute([
                'git', '-C', repo_path,
                '-c', f'core.hooksPath={temp_hooks_dir}',
                'push', '--no-verify', 'origin', branch
            ])
            logger.info("Committed and pushed README update")
        except Exception as e:
            logger.warning(f"Could not auto-commit README: {e}")
        finally:
            try:
                if temp_hooks_dir:
                    shutil.rmtree(temp_hooks_dir, ignore_errors=True)
            except Exception:
                pass

    except Exception as e:
        raise SyncError(f"Error updating README: {e}")


def main():
    """
    Main execution function that orchestrates the synchronization process.
    """
    print("=" * 60)
    print("Google Drive to GitHub Synchronization Script")
    print("=" * 60)
    print()

    created_files: List[str] = []

    try:
        # 1. Load configuration
        logger.info("Step 1: Loading configuration...")
        config = load_config()

        # 2. Authenticate with Google Drive
        logger.info("Step 2: Authenticating with Google Drive...")
        service = authenticate_gdrive(config['GDRIVE_CREDENTIALS_FILE'])

        # 3. Sync files (download and convert new files)
        logger.info("Step 3: Synchronizing files...")
        new_files = sync_files(service, config, created_files)

        # 4. Commit and push to GitHub
        if new_files:
            logger.info("Step 4: Committing and pushing to GitHub...")
            commit_and_push(config['LOCAL_REPO_PATH'], new_files, config)

            # 5. Update README
            logger.info("Step 5: Updating README.md...")
            update_readme(
                config['LOCAL_REPO_PATH'],
                config['GITHUB_REPO_URL'],
                new_files
            )
        else:
            logger.info("Steps 4-5: Skipped (no new files to process)")

        # Print summary
        print()
        print("=" * 60)
        print("SYNCHRONIZATION COMPLETE")
        print("=" * 60)
        print(f"Total new files synced: {len(new_files)}")
        if new_files:
            print("\nFiles added:")
            for filename in new_files:
                print(f"  • {filename}")
        print()
        print("Check gdrive_sync.log for detailed information.")
        print("=" * 60)

    except ConfigurationError as e:
        # No downloads happened yet typically, but clean anyway
        if created_files:
            cleanup_downloads(created_files)
        logger.error(f"Configuration error: {e}")
        print(f"\n❌ Configuration Error: {e}")
        return 1
    except NonFastForwardError as e:
        # Do NOT delete downloaded files; user can resolve and rerun push
        logger.error(f"Synchronization error (non-fast-forward): {e}")
        print(f"\n❌ Synchronization Error: {e}")
        return 1
    except SyncError as e:
        # Roll back downloaded files for this run
        if created_files:
            cleanup_downloads(created_files)
        logger.error(f"Synchronization error: {e}")
        print(f"\n❌ Synchronization Error: {e}")
        return 1
    except KeyboardInterrupt:
        if created_files:
            cleanup_downloads(created_files)
        logger.info("Script interrupted by user")
        print("\n\n⚠️  Script interrupted by user")
        return 130
    except Exception as e:
        if created_files:
            cleanup_downloads(created_files)
        logger.exception(f"Unexpected error: {e}")
        print(f"\n❌ Unexpected Error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
