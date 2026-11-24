View switcher — underline for page views (Board / List / Timeline), segmented for compact toggles.
```jsx
<Tabs items={[{id:"board",label:"Board"},{id:"list",label:"List",count:24}]} value={v} onChange={setV} />
<Tabs variant="segmented" items={[{id:"w",label:"Week"},{id:"m",label:"Month"}]} value="w" />
```
