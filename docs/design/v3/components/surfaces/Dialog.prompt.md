Centered modal, `rounded-4xl`, popover surface, dimmed overlay, ghost X top-right.

```jsx
<Dialog
  trigger={<Button variant="outline">Rename thread</Button>}
  title="Rename conversation"
  description="Give this session a memorable title."
  footer={<><Button variant="ghost">Cancel</Button><Button>Save</Button></>}
>
  <Input defaultValue="Untitled conversation" />
</Dialog>
```
