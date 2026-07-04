Form primitives. All pill-shaped (`--radius-4xl`) except Textarea (`--radius-xl`), `bg-input/30`, 3px focus ring.

```jsx
<Label htmlFor="email">Email</Label>
<Input id="email" type="email" placeholder="you@example.com" />
<Textarea placeholder="Message…" rows={4} />
<Switch defaultChecked onCheckedChange={(v) => {}} />
<Select options={['GPT-4o', 'Claude', 'Gemini']} placeholder="Model" />
```

Density: everything is `text-sm` (14px), heights 36px (sm 32px). Stack label+field with an 6px gap.
