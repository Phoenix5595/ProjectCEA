import { test, expect } from '@playwright/test';

test('Dashboard loads without console errors', async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(msg.text());
  });
  await page.goto('/');
  await expect(page.locator('.main-dashboard')).toBeVisible({ timeout: 10000 });
  expect(errors.filter((e) => !e.includes('favicon'))).toHaveLength(0);
});
