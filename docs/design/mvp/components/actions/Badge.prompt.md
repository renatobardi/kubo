Small pill chip for status and labels. Same color vocabulary as Button (`h-5`, `text-xs`, pill).

```jsx
<Badge>Running</Badge>
<Badge variant="secondary" icon="bot">Research Agent</Badge>
<Badge variant="destructive">error</Badge>
<Badge variant="outline" icon="clock">Cron</Badge>
```

Variants: default, secondary, destructive (tinted), outline, ghost. Status→variant mapping used in the app: running→default, completed→secondary, error→destructive, else→outline.
