The Kubo logo. The mark is the kanji **智 (chi, wisdom/intellect)** in a near-black tile, beside the "Kubo" wordmark. There is no separate drawn logotype.

```jsx
<Logo />                       {/* tile 智 + "Kubo" + tagline */}
<Logo variant="mark" size={40} />   {/* just the near-black 智 tile — app icon / favicon */}
<Logo variant="kanji" size={48} />  {/* 智 as a large pure typographic glyph */}
<Logo tagline="" />            {/* full lockup, no tagline */}
```

Use `variant="mark"` in the sidebar header and anywhere a compact app icon is needed; `variant="full"` on sign-in and marketing. The tile uses `--sidebar-primary` (near-black in the mono theme). Swap the glyph with `markGlyph` if desired.
