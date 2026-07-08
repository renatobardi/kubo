The signature Kubo control: pill-shaped, `text-sm font-medium`, sinks 1px on press. Use for every action.

```jsx
<Button>New agent</Button>
<Button variant="outline" icon="plus">New agent</Button>
<Button variant="destructive">Delete</Button>
<Button size="icon" variant="ghost" icon="x" />
```

Variants: default (solid near-black mono), outline, secondary, ghost, destructive (tinted 10% bg + red text — never solid), link. Sizes: default/sm/xs/lg + icon/icon-sm/icon-xs/icon-lg. Destructive is the one to remember — it's tinted, elegant on tool screens with many dangerous actions.
