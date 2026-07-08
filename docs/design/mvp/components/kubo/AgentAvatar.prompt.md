Kubo product patterns — composed from the primitives, mapped straight from the source app.

```jsx
<AgentAvatar name="Research Agent" size="lg" />

<AgentCard agent={{ name:'Research Agent', description:'Digs through your knowledge base.' }} onClick={openChat} />

<StatTile label="Agents" value={4} icon="bot" />

<ChatInput onSend={(text) => append(text)} placeholder="Message Research Agent…" />
```

Agent avatars are an image URL or the bot-icon fallback in a muted circle — no emoji. Stat tiles and agent cards share the small-card treatment (border + shadow-sm + hover primary border + `active:scale-[0.99]`) — the one place the system uses a real shadow instead of a ring.
