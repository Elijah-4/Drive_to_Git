## Where files are saved

By default, files are saved inside your repo under a subdirectory:

- Default folder name: `Google Drive Folder`
- You can change it by setting `SYNC_SUBDIR_NAME` in your `config.json`.

Example `config.json` entry:

```
"SYNC_SUBDIR_NAME": "Google Drive Folder"
```

Filenames are sanitized to be safe on Windows/macOS/Linux (e.g., characters like `/ \\ : * ? " < > |` are replaced with `_`).

---

## Sync behavior: additive only

- This tool performs additive syncing. It downloads and commits new files from Google Drive into your repo.
- Deletions in Google Drive are NOT propagated to Git. If you delete or remove a file in Drive and run the sync again, the file will remain in your Git repository.
- Example: If you add a file directly in Git, then later delete it in Drive and re-run the sync, the file will not be deleted from Git.
- To remove a file from the Git repo, delete it in the repo and commit that deletion yourself (or open a PR if you use a protected branch).

Notes
- By default, existing files in the repo are left as-is. The sync is conservative and does not overwrite files that already exist with the same name/path.
