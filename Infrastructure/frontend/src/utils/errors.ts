/** Narrow unknown caught errors into a display-safe message.
 *
 * Prefers axios-style `err.response.data.detail` (FastAPI convention), falls
 * back to `err.message`, then to the fallback string. Callers should use
 * this instead of `catch (err: any) { err.response?.data?.detail }` now that
 * @typescript-eslint/no-explicit-any is an error.
 */
export function extractErrorMessage(err: unknown, fallback = 'Unknown error'): string {
  if (typeof err === 'string') return err;
  if (err && typeof err === 'object') {
    const response = (err as { response?: unknown }).response;
    if (response && typeof response === 'object') {
      const data = (response as { data?: unknown }).data;
      if (data && typeof data === 'object') {
        const detail = (data as { detail?: unknown }).detail;
        if (typeof detail === 'string') return detail;
      }
    }
    const message = (err as { message?: unknown }).message;
    if (typeof message === 'string') return message;
  }
  return fallback;
}
