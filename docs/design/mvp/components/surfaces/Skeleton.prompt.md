Loading placeholders and tooltips.

```jsx
<Skeleton style={{ height: 16, width: 200 }} />
<Skeleton style={{ height: 40, width: 40, borderRadius: 9999 }} />

<Tooltip label="Dark Mode" side="right">
  <Button size="icon" variant="ghost" icon="moon" />
</Tooltip>
```

Tooltip bubble is `bg-foreground text-background`, `rounded-2xl`, 12px — inverted, appears on hover.
