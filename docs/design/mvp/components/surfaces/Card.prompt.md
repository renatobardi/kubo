The flat Kubo card — no shadow, no border, just a subtle `ring-1 ring-foreground/10`. Works in both themes.

```jsx
<Card>
  <CardHeader>
    <CardTitle>Your agents</CardTitle>
    <CardDescription>3 agents · pick one to start chatting</CardDescription>
    <CardAction><Button variant="outline" size="sm" icon="plus">New agent</Button></CardAction>
  </CardHeader>
  <CardContent>…</CardContent>
</Card>
```

Title is Noto Serif 16px medium. Use `size="sm"` for denser cards (kanban). For list cards, give CardContent `style={{padding:0}}` and use divided `<ul>` rows.
