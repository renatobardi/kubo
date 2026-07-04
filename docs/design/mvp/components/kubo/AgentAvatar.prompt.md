Kubo product patterns — composed from the primitives, mapped straight from the source app.

```jsx
<AgentAvatar avatar="🤖" name="Research Agent" size="lg" />

<AgentCard agent={{ name:'Research Agent', description:'Digs through your knowledge base.', avatar:'🔍' }} onClick={openChat} />

<StatTile label="Agents" value={4} icon="bot" />

<ChatInput onSend={(text) => append(text)} placeholder="Message Research Agent…" />
```

Agent avatars use the preset emoji list in `assets/agent-emojis.js`. Stat tiles and agent cards share the small-card treatment (border + shadow-sm + hover amber border + `active:scale-[0.99]`) — the one place the system uses a real shadow instead of a ring.
