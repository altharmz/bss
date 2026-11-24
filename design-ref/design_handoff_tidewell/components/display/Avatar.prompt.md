Circular person marker with deterministic tinted initials; use for assignees, owners and members.
```jsx
<Avatar name="Maya Okafor" />
<div style={{display:"flex"}}>{people.map((p,i)=><Avatar key={p} name={p} ring style={{marginLeft:i?-6:0}}/>)}</div>
```
- Sizes: 20 (dense rows), 28 (default), 40 (profile). Use `ring` when stacking.
