Modal panel with serif title, 20px radius and a tinted footer action row; blurred ink overlay.
```jsx
<Dialog open={o} onClose={close} title="New task" footer={<><Button variant="ghost" onClick={close}>Cancel</Button><Button>Create task</Button></>}>
  <Input label="Title" />
</Dialog>
```
