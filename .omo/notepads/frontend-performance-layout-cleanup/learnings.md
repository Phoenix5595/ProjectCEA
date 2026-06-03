## Frontend Performance & Layout Cleanup
- Parallelized API calls in `Dashboard.tsx` to reduce initial load time.
- Updated column widths to 37%/37%/26% using flexbox for better responsive behavior.
- Added 10s timeout to `backendClient` in `api.ts` to prevent hanging requests.
- Cleaned up broken/redundant `clusterA/B` API calls and processing.
- Fixed several JSX syntax errors and tag mismatches introduced during large file edits.
