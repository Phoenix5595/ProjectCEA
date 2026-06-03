### Self-hosting Fonts in Vite/Tailwind 4
- Using `@fontsource` packages is a reliable way to get correct woff2 files for self-hosting.
- In Tailwind CSS 4, `@theme inline` in `index.css` is used to define custom font families.
- Preloading fonts in `index.html` improves performance by reducing layout shift.
- Implemented a dev-only ThemeSwitcher component using `import.meta.env.DEV` for tree-shaking safety.
- Integrated the switcher in `App.tsx` within the `ThemeProvider` to ensure it has access to the theme context.
- Verified that `npm run build` passes, confirming that the `import.meta.env.DEV` guard works as expected with Vite/TypeScript.
