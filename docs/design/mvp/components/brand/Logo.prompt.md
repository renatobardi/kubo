The Kubo logo. The mark is a **five-petal sakura (cherry blossom)** drawn in line style — mono, near-black — beside the "Kubo" wordmark. A nod to the "atelier" idea; no colored variant.

```jsx
<Logo />                       {/* loose line sakura + "Kubo" + tagline */}
<Logo variant="mark" size={40} />   {/* sakura in a near-black tile — app icon / favicon */}
<Logo variant="glyph" size={48} />  {/* the bare blossom on its own */}
<Logo tagline="" />            {/* full lockup, no tagline */}
<Sakura size={30} stroke="currentColor" />  {/* just the SVG blossom, reusable */}
```

Use `variant="mark"` (tile) where a compact app icon is needed; `variant="full"` on sign-in, sidebar header, and marketing — there the blossom sits loose (no tile) next to the wordmark. The tile uses `--sidebar-primary` (near-black in the mono theme).
