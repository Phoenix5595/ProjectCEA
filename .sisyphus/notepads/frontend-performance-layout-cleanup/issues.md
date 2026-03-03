## Issues Encountered
- `cat` command with large heredocs in the `bash` tool was truncated at around 777 lines, leading to broken files.
- `write` tool refused to overwrite existing files, requiring manual emptying or `edit` tool.
- Complexity of `Dashboard.tsx` (~1200 lines) made it difficult to manage tag closures without a full file rewrite.
