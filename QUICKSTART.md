# Quick Start Guide - Google Drive to GitHub Sync

## Step 1: Create Your Configuration File

Copy the template configuration and fill in your details:

Windows CMD:
```cmd
copy config.template.json config.json
```

PowerShell:
```powershell
Copy-Item config.template.json config.json
```

Then edit `config.json` with your actual values:

```json
{
  "GOOGLE_DRIVE_FOLDER_ID": "1a2b3c4d5e6f7g8h9i0j",
  "LOCAL_REPO_PATH": "C:\\Users\\yourname\\path\\to\\repo",
  "GITHUB_REPO_URL": "https://github.com/yourusername/yourrepo",
  "GDRIVE_CREDENTIALS_FILE": "credentials.json",
  "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_your_token_here",
  "SYNC_SUBDIR_NAME": "Google Drive Folder"
}
```

### How to Get Your Google Drive Folder ID:
1. Open Google Drive in your browser
2. Navigate to the folder you want to sync
3. Look at the URL: `https://drive.google.com/drive/folders/FOLDER_ID_HERE`
4. Copy the FOLDER_ID_HERE part

### How to Get Your GitHub Personal Access Token:
1. Go to GitHub.com → Settings → Developer settings
2. Click "Personal access tokens" → "Tokens (classic)"
3. Click "Generate new token (classic)"
4. Give it a name like "Drive Sync Script"
5. Select scopes: Check `repo` (Full control of private repositories)
6. Click "Generate token" and copy it immediately

## Step 2: Run the Script

```cmd
python gdrive_to_github_sync.py
```

### First Run:
- A browser window will open asking you to authorize the app
- Log in with your Google account
- Grant the requested permissions
- The script will create a `token.json` file for future runs

### Subsequent Runs:
- The script will use the saved token
- It will only download NEW files (idempotent)
- It is additive only: deletions in Google Drive are NOT propagated to your Git repository

## Step 3: Check the Results

- **Console Output**: Shows progress and summary
- **gdrive_sync.log**: Detailed logs of all operations
- **Your Git Repo**: Check for new files and commits
- **GitHub**: Verify the push was successful
- **README.md**: Should contain links to the synced files

---

## Important: Additive-only sync

- This tool only adds new files from Google Drive to your repository.
- If a file is removed from Google Drive, it will NOT be deleted from your Git repo on the next sync.
- Example: If you add a file directly in Git, then delete it in Drive and re-run the sync, the file will remain in Git.
- To remove a file from Git, delete it in your repository and commit that deletion (or open a PR if your branch is protected).

## What Files Are Converted?

| Google Type | Converted To |
|-------------|--------------|
| Google Docs | Markdown (.md) |
| Google Sheets | CSV (.csv) |
| Google Slides | PDF (.pdf) |
| Other files | Downloaded as-is |

## Optional: Choose a destination subfolder

All synced files are saved into a subfolder inside your repo. Default is `Google Drive Folder`. You can change it in `config.json`:

```
"SYNC_SUBDIR_NAME": "Google Drive Folder"
```

Filenames are automatically sanitized (e.g., `Touchpoint meeting 10/9` becomes `Touchpoint meeting 10_9`).

## Troubleshooting

### "Configuration file not found"
The script will create `config.json` for you on first run. Fill it in with your details.

### "Credentials file not found"
Make sure `credentials.json` is in the Drive_to_Git folder.

### Git push fails
- Verify your GitHub PAT has `repo` permissions
- Make sure your local repo has a remote configured
- Check that you have write access to the repository

## Automation

### Schedule with Windows Task Scheduler:
1. Open Task Scheduler
2. Create Basic Task
3. Set trigger (daily, weekly, etc.)
4. Action: Start a program
5. Program: `python`
6. Arguments: `"C:\\Users\\elija\\PycharmProjects\\pythonProject\\Drive_to_Git\\gdrive_to_github_sync.py"`
7. Start in: `C:\\Users\\elija\\PycharmProjects\\pythonProject\\Drive_to_Git`

---

**Happy Syncing! 🚀**
