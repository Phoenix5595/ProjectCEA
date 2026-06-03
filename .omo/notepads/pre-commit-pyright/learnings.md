### Pre-commit Non-blocking Hooks
To implement a pre-commit hook that warns but does not block the commit (even on non-zero exit codes):
1. Override the `entry` with a shell wrapper: `entry: bash -c 'command "$@" || true' --`
2. Set `verbose: true` to ensure output is visible even on "success" (exit 0).
3. Keep `args` as usual; they will be passed to the shell wrapper via `"$@"`.
