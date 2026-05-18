  Recommended Prompt Sample
  You can use this exact prompt whenever you move a session:

  > "I need to import a Codex session from an external drive. Please perform a path-aware migration for session ID
  [SESSION_ID] from [SOURCE_PATH] to my local Codex directory.
  >
  > 1. Find and replace all occurrences of the old machine's path ([OLD_PATH]) with my current local path ([NEW_PATH])
  inside the .jsonl rollout and the .sh shell snapshot.
  > 2. Ensure you also update any old folder names if they differ (e.g., change 'Project-Main' to 'Project').
  > 3. Clear all macOS extended attributes (xattrs) from the files to prevent 'Permission denied' errors.
  > 4. Update my local session_index.jsonl so the session appears in my history."

  ---

  Why this works
   - The .jsonl file controls where the agent thinks it is (the cwd property). If this is wrong, it will try to read
     files that don't exist.
   - The .sh file restores the terminal state. If it contains the wrong username or paths, the terminal will crash with
     "Permission denied" or "Command not found."
   - xattrs are security "tags" macOS adds to files from external drives. Clearing them removes the "quarantine" that
     often causes the machine-level blockers you saw earlier.

