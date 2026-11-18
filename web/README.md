# Aries — Web

A React + Vite web front for Aries, the PC desktop companion. Its hero is
the **DitherVeil** component from React Bits: a WebGL dithered image that
reveals full colour around your cursor.

## Run it

```bash
cd web
npm install
npm run dev
```

Then open the address Vite prints (usually http://localhost:5173).

## Build it

```bash
npm run build
npm run preview
```

## Files

- `src/DitherVeil.jsx` — the DitherVeil component (React Bits, JavaScript + CSS
  variant). The `'use client'` directive was dropped; it is not needed in Vite.
- `src/DitherVeil.css` — the component's styles.
- `src/App.jsx` — the Aries hero page: the veil as a full-screen backdrop
  with the "Aries active. Ready to cook." greeting over it.
- `src/main.jsx` — Vite entry point.

## Notes

- Dependency: `ogl` (WebGL). The component needs a browser with WebGL2.
- The default image is an Unsplash photo; it must be served with CORS
  headers (Unsplash images are). Swap `src` in `App.jsx` for any image URL.
- This is a showcase page for now. It does not talk to the Python assistant
  (`aries/`); wiring the web UI to the assistant backend is a later step.
