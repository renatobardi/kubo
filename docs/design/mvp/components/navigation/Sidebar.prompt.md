App-shell navigation. Sidebar is 256px, uses the `--sidebar-*` token set (vivid amber active state); Breadcrumb + PageHeader live in the content area.

```jsx
<Sidebar active="Chat" onNavigate={(t) => setPage(t)} user={{name:'Alex Rivera', email:'alex@kubo.ai'}} />

<Breadcrumb segments={[{label:'Chat', href:'#'}, {label:'Research Agent'}]} />

<PageHeader title="Agents" description="Create and manage your AI agents."
  actions={<Button icon="plus">New agent</Button>} />
```

Shell layout: sidebar + inset; 72px header (`border-b border/50`) holds a panel-left trigger, a 1px×16px divider, then the breadcrumb; main is `p-6 gap-6`.
